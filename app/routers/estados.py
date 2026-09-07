from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import auth, models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/estados", tags=["Estados"])


@router.get("", response_model=List[schemas.EstadoMateriaOut])
def listar_estados(
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Lista el estado actual de todas las materias PARA EL USUARIO LOGUEADO.
    Una materia sin fila todavía se interpreta como NO_CURSADA en el
    frontend (no hace falta que exista una fila para cada materia).
    """
    return db.query(models.EstadoMateria).filter(models.EstadoMateria.usuario_id == usuario.id).all()


@router.get("/{materia_id}", response_model=schemas.EstadoMateriaOut)
def obtener_estado(
    materia_id: int,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Obtiene el estado de una materia específica para el usuario logueado."""
    estado = db.query(models.EstadoMateria).filter(
        models.EstadoMateria.materia_id == materia_id,
        models.EstadoMateria.usuario_id == usuario.id,
    ).first()
    if not estado:
        raise HTTPException(status_code=404, detail="Todavía no marcaste un estado para esta materia")
    return estado


@router.post("/reset", response_model=List[schemas.EstadoMateriaOut])
async def resetear_estados(
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Reinicia el avance académico del usuario logueado (solo el suyo):
    pone todas SUS materias en NO_CURSADA. Emite un evento WebSocket con la
    lista completa para que sus otros dispositivos conectados se resincronicen.
    """
    estados = db.query(models.EstadoMateria).filter(models.EstadoMateria.usuario_id == usuario.id).all()
    for e in estados:
        e.estado = models.EstadoEnum.NO_CURSADA
    db.commit()

    salida = [schemas.EstadoMateriaOut.from_orm(e) for e in estados]
    await manager.broadcast("estados_reseteados", {"usuario_id": usuario.id, "estados": [e.dict() for e in salida]})
    return salida


@router.put("/{materia_id}", response_model=schemas.EstadoMateriaOut)
async def actualizar_estado(
    materia_id: int,
    datos: schemas.EstadoMateriaUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Actualiza (o crea, si es la primera vez) el estado de una materia para
    el usuario logueado. Emite un evento WebSocket a todos los clientes
    conectados (incluye usuario_id para que cada frontend filtre lo suyo).
    """
    materia = db.query(models.Materia).filter(models.Materia.id == materia_id).first()
    if not materia:
        raise HTTPException(status_code=404, detail="Materia no encontrada")

    estado = db.query(models.EstadoMateria).filter(
        models.EstadoMateria.materia_id == materia_id,
        models.EstadoMateria.usuario_id == usuario.id,
    ).first()

    if not estado:
        estado = models.EstadoMateria(materia_id=materia_id, usuario_id=usuario.id, estado=datos.estado)
        db.add(estado)
    else:
        estado.estado = datos.estado

    db.commit()
    db.refresh(estado)

    out = schemas.EstadoMateriaOut.from_orm(estado)
    await manager.broadcast("estado_actualizado", out.dict())
    return estado

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/estados", tags=["Estados"])


@router.get("", response_model=list[schemas.EstadoMateriaOut])
def listar_estados(db: Session = Depends(get_db)):
    """Lista el estado actual de todas las materias."""
    return db.query(models.EstadoMateria).all()


@router.get("/{materia_id}", response_model=schemas.EstadoMateriaOut)
def obtener_estado(materia_id: int, db: Session = Depends(get_db)):
    """Obtiene el estado de una materia específica."""
    estado = db.query(models.EstadoMateria).filter(
        models.EstadoMateria.materia_id == materia_id
    ).first()
    if not estado:
        raise HTTPException(status_code=404, detail="Materia no encontrada")
    return estado


@router.post("/reset", response_model=list[schemas.EstadoMateriaOut])
async def resetear_estados(db: Session = Depends(get_db)):
    """Reinicia el avance académico completo: pone todas las materias en
    NO_CURSADA. Emite un único evento WebSocket con la lista completa para
    que todos los clientes conectados se resincronicen de una.
    """
    estados = db.query(models.EstadoMateria).all()
    for e in estados:
        e.estado = models.EstadoEnum.NO_CURSADA
    db.commit()

    salida = [schemas.EstadoMateriaOut.model_validate(e) for e in db.query(models.EstadoMateria).all()]
    await manager.broadcast("estados_reseteados", [e.model_dump() for e in salida])
    return salida


@router.put("/{materia_id}", response_model=schemas.EstadoMateriaOut)
async def actualizar_estado(
    materia_id: int,
    datos: schemas.EstadoMateriaUpdate,
    db: Session = Depends(get_db),
):
    """Actualiza el estado de una materia (PROMOCIONADA / REGULAR / NO_CURSADA).
    Emite un evento WebSocket a todos los clientes conectados.
    """
    estado = db.query(models.EstadoMateria).filter(
        models.EstadoMateria.materia_id == materia_id
    ).first()
    if not estado:
        raise HTTPException(status_code=404, detail="Materia no encontrada")

    estado.estado = datos.estado
    db.commit()
    db.refresh(estado)

    out = schemas.EstadoMateriaOut.model_validate(estado)
    await manager.broadcast("estado_actualizado", out.model_dump())
    return estado

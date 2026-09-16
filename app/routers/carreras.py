from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import auth, models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/carreras", tags=["Carreras"])


@router.get("", response_model=List[schemas.CarreraOut])
def listar_carreras(
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Lista todas las carreras cargadas (cualquier usuario logueado puede
    verlas, para elegir cuál está viendo/trackeando)."""
    return db.query(models.Carrera).order_by(models.Carrera.nombre).all()


@router.post("", response_model=schemas.CarreraOut, status_code=201)
async def crear_carrera(
    datos: schemas.CarreraCreate,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Crea una nueva carrera (sólo ADMIN). Una vez creada, se le pueden
    cargar materias y correlatividades propias desde el panel de Admin."""
    existente = db.query(models.Carrera).filter(models.Carrera.nombre == datos.nombre).first()
    if existente:
        raise HTTPException(status_code=400, detail=f"Ya existe una carrera llamada '{datos.nombre}'")

    carrera = models.Carrera(**datos.dict())
    db.add(carrera)
    db.commit()
    db.refresh(carrera)

    await manager.broadcast("carrera_creada", schemas.CarreraOut.from_orm(carrera).dict())
    return carrera


@router.put("/{carrera_id}", response_model=schemas.CarreraOut)
async def actualizar_carrera(
    carrera_id: int,
    datos: schemas.CarreraUpdate,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Actualiza el nombre/plan/excepción de una carrera (sólo ADMIN)."""
    carrera = db.query(models.Carrera).filter(models.Carrera.id == carrera_id).first()
    if not carrera:
        raise HTTPException(status_code=404, detail="Carrera no encontrada")

    for campo, valor in datos.dict(exclude_unset=True).items():
        setattr(carrera, campo, valor)

    db.commit()
    db.refresh(carrera)

    await manager.broadcast("carrera_actualizada", schemas.CarreraOut.from_orm(carrera).dict())
    return carrera


@router.delete("/{carrera_id}", status_code=204)
async def eliminar_carrera(
    carrera_id: int,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Elimina una carrera y TODO lo que dependa de ella: sus materias,
    correlatividades, progreso de usuarios en esas materias, y su
    configuración de año/cuatrimestre (sólo ADMIN, no se puede deshacer)."""
    carrera = db.query(models.Carrera).filter(models.Carrera.id == carrera_id).first()
    if not carrera:
        raise HTTPException(status_code=404, detail="Carrera no encontrada")

    db.delete(carrera)
    db.commit()

    await manager.broadcast("carrera_eliminada", {"id": carrera_id})

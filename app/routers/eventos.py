from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app import auth, models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/eventos", tags=["Agenda"])


@router.get("", response_model=List[schemas.EventoOut])
def listar_eventos(
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Lista eventos de la agenda compartida en un rango de fechas.

    Sin parámetros, devuelve un rango por defecto (hoy -30 / +90 días) para
    no traer de una los ~1300 eventos históricos importados del calendario UTN.
    """
    if desde is None:
        desde = date.today() - timedelta(days=30)
    if hasta is None:
        hasta = date.today() + timedelta(days=90)

    return (
        db.query(models.Evento)
        .filter(models.Evento.fecha >= desde, models.Evento.fecha <= hasta)
        .order_by(models.Evento.fecha, models.Evento.hora_inicio)
        .all()
    )


@router.post("", response_model=schemas.EventoOut, status_code=201)
async def crear_evento(
    datos: schemas.EventoCreate,
    db: Session = Depends(get_db),
    admin: models.Usuario = Depends(auth.require_admin),
):
    """Crea un evento manual en la agenda compartida (sólo ADMIN)."""
    evento = models.Evento(
        **datos.dict(),
        origen=models.OrigenEvento.MANUAL,
        creado_por_id=admin.id,
    )
    db.add(evento)
    db.commit()
    db.refresh(evento)

    await manager.broadcast("evento_creado", schemas.EventoOut.from_orm(evento).dict())
    return evento


@router.put("/{evento_id}", response_model=schemas.EventoOut)
async def actualizar_evento(
    evento_id: int,
    datos: schemas.EventoUpdate,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Actualiza un evento existente, sea manual o importado (sólo ADMIN)."""
    evento = db.query(models.Evento).filter(models.Evento.id == evento_id).first()
    if not evento:
        raise HTTPException(status_code=404, detail="Evento no encontrado")

    for campo, valor in datos.dict(exclude_unset=True).items():
        setattr(evento, campo, valor)

    db.commit()
    db.refresh(evento)

    await manager.broadcast("evento_actualizado", schemas.EventoOut.from_orm(evento).dict())
    return evento


@router.delete("/{evento_id}", status_code=204)
async def eliminar_evento(
    evento_id: int,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Elimina un evento de la agenda (sólo ADMIN)."""
    evento = db.query(models.Evento).filter(models.Evento.id == evento_id).first()
    if not evento:
        raise HTTPException(status_code=404, detail="Evento no encontrado")

    db.delete(evento)
    db.commit()

    await manager.broadcast("evento_eliminado", {"id": evento_id})

from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app import auth, models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/eventos", tags=["Agenda"])


def _es_admin(usuario: models.Usuario) -> bool:
    return usuario.rol == models.RolEnum.ADMIN


@router.get("", response_model=List[schemas.EventoOut])
def listar_eventos(
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Lista los eventos institucionales (para todos) más los eventos
    personales del usuario logueado, en un rango de fechas.

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
        .filter(or_(models.Evento.personal == False, models.Evento.creado_por_id == usuario.id))  # noqa: E712
        .order_by(models.Evento.fecha, models.Evento.hora_inicio)
        .all()
    )


@router.post("", response_model=schemas.EventoOut, status_code=201)
async def crear_evento(
    datos: schemas.EventoCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Crea un evento. Uno institucional (compartido con todos) sólo lo puede
    crear un ADMIN; uno personal lo puede crear cualquier usuario logueado,
    y sólo él lo va a ver."""
    if not datos.personal and not _es_admin(usuario):
        raise HTTPException(
            status_code=403,
            detail="Sólo el administrador puede crear eventos institucionales. Marcá 'evento personal' para agendar algo solo para vos.",
        )

    evento = models.Evento(
        **datos.dict(),
        origen=models.OrigenEvento.MANUAL,
        creado_por_id=usuario.id,
    )
    db.add(evento)
    db.commit()
    db.refresh(evento)

    salida = schemas.EventoOut.from_orm(evento).dict()
    if evento.personal:
        # Sólo a los dispositivos de quien lo creó: nadie más debe enterarse.
        await manager.enviar_a_usuarios([usuario.id], "evento_creado", salida)
    else:
        await manager.broadcast("evento_creado", salida)
    return evento


def _validar_permiso_edicion(evento: models.Evento, usuario: models.Usuario, datos: schemas.EventoUpdate) -> None:
    if evento.personal:
        if evento.creado_por_id != usuario.id and not _es_admin(usuario):
            raise HTTPException(status_code=403, detail="Ese evento es personal de otro usuario")
    elif not _es_admin(usuario):
        raise HTTPException(status_code=403, detail="Sólo el administrador puede modificar eventos institucionales")

    # Cambiar si un evento es personal o institucional equivale a decidir
    # quién lo puede ver: eso lo maneja sólo el ADMIN.
    if datos.personal is not None and datos.personal != evento.personal and not _es_admin(usuario):
        raise HTTPException(status_code=403, detail="Sólo el administrador puede cambiar si un evento es personal o institucional")


@router.put("/{evento_id}", response_model=schemas.EventoOut)
async def actualizar_evento(
    evento_id: int,
    datos: schemas.EventoUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Actualiza un evento existente, sea institucional o personal."""
    evento = db.query(models.Evento).filter(models.Evento.id == evento_id).first()
    if not evento:
        raise HTTPException(status_code=404, detail="Evento no encontrado")

    _validar_permiso_edicion(evento, usuario, datos)

    for campo, valor in datos.dict(exclude_unset=True).items():
        setattr(evento, campo, valor)

    db.commit()
    db.refresh(evento)

    salida = schemas.EventoOut.from_orm(evento).dict()
    if evento.personal:
        destinatarios = {usuario.id}
        if evento.creado_por_id is not None:
            destinatarios.add(evento.creado_por_id)
        await manager.enviar_a_usuarios(destinatarios, "evento_actualizado", salida)
    else:
        await manager.broadcast("evento_actualizado", salida)
    return evento


@router.delete("/{evento_id}", status_code=204)
async def eliminar_evento(
    evento_id: int,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Elimina un evento, sea institucional o personal."""
    evento = db.query(models.Evento).filter(models.Evento.id == evento_id).first()
    if not evento:
        raise HTTPException(status_code=404, detail="Evento no encontrado")

    if evento.personal:
        if evento.creado_por_id != usuario.id and not _es_admin(usuario):
            raise HTTPException(status_code=403, detail="Ese evento es personal de otro usuario")
    elif not _es_admin(usuario):
        raise HTTPException(status_code=403, detail="Sólo el administrador puede borrar eventos institucionales")

    era_personal = evento.personal
    dueño_id = evento.creado_por_id
    db.delete(evento)
    db.commit()

    if era_personal:
        destinatarios = {usuario.id}
        if dueño_id is not None:
            destinatarios.add(dueño_id)
        await manager.enviar_a_usuarios(destinatarios, "evento_eliminado", {"id": evento_id})
    else:
        await manager.broadcast("evento_eliminado", {"id": evento_id})

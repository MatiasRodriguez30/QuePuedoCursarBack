from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app import auth, models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/config", tags=["Configuración"])


def _get_or_create(db: Session, carrera_id: int) -> models.ConfigApp:
    cfg = db.query(models.ConfigApp).filter(models.ConfigApp.carrera_id == carrera_id).first()
    if not cfg:
        cfg = models.ConfigApp(carrera_id=carrera_id, anio_actual=None, cuatrimestre_actual=None)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@router.get("", response_model=schemas.ConfigOut)
def obtener_config(
    carrera_id: int = Query(...),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Devuelve el año/cuatrimestre actual configurado para esta carrera (lo
    fija el admin). Si nunca se configuró, ambos campos vienen null (el
    frontend cae entonces a estimarlo con la fecha del dispositivo).
    """
    return _get_or_create(db, carrera_id)


@router.put("", response_model=schemas.ConfigOut)
async def actualizar_config(
    datos: schemas.ConfigUpdate,
    carrera_id: int = Query(...),
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Actualiza el año/cuatrimestre actual de una carrera (sólo ADMIN). Se
    sincroniza a todos los clientes conectados vía WebSocket."""
    if not db.query(models.Carrera).filter(models.Carrera.id == carrera_id).first():
        raise HTTPException(status_code=404, detail="Carrera no encontrada")

    cfg = _get_or_create(db, carrera_id)
    cfg.anio_actual = datos.anio_actual
    cfg.cuatrimestre_actual = datos.cuatrimestre_actual
    db.commit()
    db.refresh(cfg)

    out = schemas.ConfigOut.from_orm(cfg)
    await manager.broadcast("config_actualizada", out.dict())
    return cfg

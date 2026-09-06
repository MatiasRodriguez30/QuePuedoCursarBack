from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/config", tags=["Configuración"])


def _get_or_create(db: Session) -> models.ConfigApp:
    cfg = db.query(models.ConfigApp).filter(models.ConfigApp.id == 1).first()
    if not cfg:
        cfg = models.ConfigApp(id=1, anio_actual=None, cuatrimestre_actual=None)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@router.get("", response_model=schemas.ConfigOut)
def obtener_config(db: Session = Depends(get_db)):
    """Devuelve el año/cuatrimestre actual configurado por el alumno.
    Si nunca se configuró, ambos campos vienen null (el frontend cae
    entonces a estimarlo con la fecha del dispositivo).
    """
    return _get_or_create(db)


@router.put("", response_model=schemas.ConfigOut)
async def actualizar_config(datos: schemas.ConfigUpdate, db: Session = Depends(get_db)):
    """Actualiza el año/cuatrimestre actual. Se sincroniza a todos los
    clientes conectados vía WebSocket."""
    cfg = _get_or_create(db)
    cfg.anio_actual = datos.anio_actual
    cfg.cuatrimestre_actual = datos.cuatrimestre_actual
    db.commit()
    db.refresh(cfg)

    out = schemas.ConfigOut.from_orm(cfg)
    await manager.broadcast("config_actualizada", out.dict())
    return cfg

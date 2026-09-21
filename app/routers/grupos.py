from fastapi import APIRouter, Depends, HTTPException, Request, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app import auth, grupos_service, models, schemas
from app.database import get_db

router = APIRouter(prefix="/grupos", tags=["Grupos"])
limiter = Limiter(key_func=get_remote_address)


def _membresia(db: Session, usuario: models.Usuario):
    return db.query(models.Membresia).filter(models.Membresia.usuario_id == usuario.id).first()


@router.post("", response_model=schemas.GrupoOut, status_code=201)
async def crear_grupo(
    datos: schemas.GrupoCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Crea un grupo y deja al usuario como su primer miembro. Cada usuario
    está en a lo sumo un grupo."""
    if _membresia(db, usuario):
        raise HTTPException(status_code=409, detail="Ya estás en un grupo. Salí de ese grupo para crear otro.")
    grupo = models.Grupo(nombre=datos.nombre, codigo=grupos_service.generar_codigo(db))
    db.add(grupo)
    db.flush()
    db.add(models.Membresia(grupo_id=grupo.id, usuario_id=usuario.id, comparte=True))
    db.commit()
    db.refresh(grupo)
    return grupos_service.grupo_payload(grupo, usuario)


@router.post("/unirse", response_model=schemas.GrupoOut)
@limiter.limit("5/minute")
async def unirse_a_grupo(
    request: Request,
    datos: schemas.GrupoUnirse,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Entra a un grupo con su código de invitación. Los intentos están
    limitados para que no se puedan adivinar códigos por fuerza bruta."""
    if _membresia(db, usuario):
        raise HTTPException(status_code=409, detail="Ya estás en un grupo. Salí de ese grupo para unirte a otro.")
    grupo = db.query(models.Grupo).filter(models.Grupo.codigo == datos.codigo.strip().upper()).first()
    if grupo is None:
        raise HTTPException(status_code=404, detail="Código inválido")
    db.add(models.Membresia(grupo_id=grupo.id, usuario_id=usuario.id, comparte=True))
    db.commit()
    db.refresh(grupo)
    await grupos_service.emitir_miembros(grupo)
    return grupos_service.grupo_payload(grupo, usuario)


@router.get("/mio", response_model=schemas.GrupoOut)
def mi_grupo(
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    membresia = _membresia(db, usuario)
    if membresia is None:
        raise HTTPException(status_code=404, detail="No estás en ningún grupo")
    return grupos_service.grupo_payload(membresia.grupo, usuario)


@router.put("/mio/preferencias", response_model=schemas.GrupoOut)
async def actualizar_preferencias(
    datos: schemas.GrupoPreferenciasUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Activa o desactiva compartir el progreso con el grupo. Quien no
    comparte sigue siendo miembro, pero no envía ni recibe logros."""
    membresia = _membresia(db, usuario)
    if membresia is None:
        raise HTTPException(status_code=404, detail="No estás en ningún grupo")
    membresia.comparte = datos.comparte
    db.commit()
    db.refresh(membresia.grupo)
    await grupos_service.emitir_miembros(membresia.grupo)
    return grupos_service.grupo_payload(membresia.grupo, usuario)


@router.post("/salir", status_code=204)
async def salir_del_grupo(
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Sale del grupo. Si era el último miembro, el grupo se elimina."""
    membresia = _membresia(db, usuario)
    if membresia is None:
        raise HTTPException(status_code=404, detail="No estás en ningún grupo")
    grupo = membresia.grupo
    db.delete(membresia)
    db.commit()
    db.refresh(grupo)
    if not grupo.miembros:
        db.delete(grupo)
        db.commit()
    else:
        await grupos_service.emitir_miembros(grupo)
    return Response(status_code=204)

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import auth, models, schemas

router = APIRouter(prefix="/usuarios", tags=["Usuarios"])


@router.get("", response_model=List[schemas.UsuarioAdminOut])
def listar_usuarios(
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Lista todos los usuarios registrados (sólo ADMIN). Reemplaza tener que
    entrar por SSH a correr scripts/make_admin.py para ver quién se registró."""
    return db.query(models.Usuario).order_by(models.Usuario.creado_en).all()


@router.put("/{usuario_id}/rol", response_model=schemas.UsuarioAdminOut)
def cambiar_rol(
    usuario_id: int,
    datos: schemas.UsuarioRolUpdate,
    db: Session = Depends(get_db),
    admin: models.Usuario = Depends(auth.require_admin),
):
    """Cambia el rol de un usuario (sólo ADMIN). Un admin no puede quitarse
    el rol a sí mismo (para evitar quedarse afuera por accidente sin nadie
    más con acceso al panel de administración en el momento)."""
    if usuario_id == admin.id and datos.rol != models.RolEnum.ADMIN:
        raise HTTPException(status_code=400, detail="No podés quitarte el rol de administrador a vos mismo")

    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    usuario.rol = datos.rol
    db.commit()
    db.refresh(usuario)
    return usuario

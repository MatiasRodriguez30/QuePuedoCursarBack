"""
Autenticación simple y gratuita: contraseñas hasheadas con PBKDF2-HMAC-SHA256
(módulo `hashlib` de la librería estándar — sin bcrypt/argon2, que tienen
extensiones nativas que no compilan fácil en la tablet ARM) y tokens de
sesión opacos guardados en la base (sin JWT, un dependencia menos).
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.database import get_db

PBKDF2_ITERATIONS = 200_000
SESION_DURACION = timedelta(days=30)

# Emails con rol ADMIN de entrada (pueden editar materias/prerequisitos/config).
# Cualquier otro email que se registre queda como usuario normal (solo su propio
# progreso). Comparación siempre en minúscula: ver normalizar_email().
ADMIN_EMAILS = {"l390585@gmail.com", "mikapaco14@gmail.com"}


def normalizar_email(email: str) -> str:
    return email.strip().lower()


def rol_para_email(email: str) -> "models.RolEnum":
    return models.RolEnum.ADMIN if normalizar_email(email) in ADMIN_EMAILS else models.RolEnum.USER


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"{salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, hex_digest = stored_hash.split("$", 1)
    except ValueError:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return secrets.compare_digest(derived.hex(), hex_digest)


def crear_sesion(db: Session, usuario: models.Usuario) -> models.Sesion:
    token = secrets.token_urlsafe(32)
    sesion = models.Sesion(
        token=token,
        usuario_id=usuario.id,
        expira_en=datetime.utcnow() + SESION_DURACION,
    )
    db.add(sesion)
    db.commit()
    return sesion


def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> models.Usuario:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No autenticado")

    token = authorization[len("Bearer "):].strip()  # str.removeprefix es 3.9+, la tablet corre 3.8
    sesion = db.query(models.Sesion).filter(models.Sesion.token == token).first()
    if not sesion:
        raise HTTPException(status_code=401, detail="Sesión inválida")
    if sesion.expira_en < datetime.utcnow():
        db.delete(sesion)
        db.commit()
        raise HTTPException(status_code=401, detail="Sesión expirada, volvé a iniciar sesión")

    usuario = db.query(models.Usuario).filter(models.Usuario.id == sesion.usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return usuario


RESET_TOKEN_DURACION = timedelta(hours=1)


def crear_reset_token(db: Session, usuario: models.Usuario) -> models.PasswordResetToken:
    token = secrets.token_urlsafe(32)
    reset = models.PasswordResetToken(
        token=token,
        usuario_id=usuario.id,
        expira_en=datetime.utcnow() + RESET_TOKEN_DURACION,
    )
    db.add(reset)
    db.commit()
    return reset


def require_admin(usuario: models.Usuario = Depends(get_current_user)) -> models.Usuario:
    if usuario.rol != models.RolEnum.ADMIN:
        raise HTTPException(status_code=403, detail="Sólo el administrador puede hacer esto")
    return usuario

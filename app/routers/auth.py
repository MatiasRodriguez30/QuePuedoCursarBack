from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import auth, models, schemas
from app.config import FRONTEND_URL
from app.database import get_db
from app.email_utils import enviar_email

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=schemas.TokenOut, status_code=201)
def registrar(datos: schemas.RegisterRequest, db: Session = Depends(get_db)):
    """Crea una cuenta nueva. El email decide el rol: los que están en la
    lista ADMIN_EMAILS (ver app/auth.py) quedan ADMIN, el resto USER."""
    email = auth.normalizar_email(datos.email)
    if not datos.password or len(datos.password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

    existente = db.query(models.Usuario).filter(models.Usuario.email == email).first()
    if existente:
        raise HTTPException(status_code=400, detail="Ya existe una cuenta con ese email")

    usuario = models.Usuario(
        email=email,
        password_hash=auth.hash_password(datos.password),
        rol=auth.rol_para_email(email),
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    sesion = auth.crear_sesion(db, usuario)
    return schemas.TokenOut(token=sesion.token, usuario=schemas.UsuarioOut.from_orm(usuario))


@router.post("/login", response_model=schemas.TokenOut)
def login(datos: schemas.LoginRequest, db: Session = Depends(get_db)):
    email = auth.normalizar_email(datos.email)
    usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()
    if not usuario or not auth.verify_password(datos.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    sesion = auth.crear_sesion(db, usuario)
    return schemas.TokenOut(token=sesion.token, usuario=schemas.UsuarioOut.from_orm(usuario))


@router.get("/me", response_model=schemas.UsuarioOut)
def yo(usuario: models.Usuario = Depends(auth.get_current_user)):
    return usuario


@router.post("/logout", status_code=204)
def logout(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),  # valida que el token sea legítimo
):
    """Invalida la sesión actual (el token deja de servir)."""
    token = authorization[len("Bearer "):].strip()
    db.query(models.Sesion).filter(models.Sesion.token == token).delete()
    db.commit()


@router.post("/forgot-password", status_code=202)
def olvide_password(
    datos: schemas.ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Envía un mail con un link para restablecer la contraseña, si el email
    existe. Siempre responde igual (no revela si la cuenta existe o no)."""
    email = auth.normalizar_email(datos.email)
    usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()

    if usuario:
        reset = auth.crear_reset_token(db, usuario)
        link = f"{FRONTEND_URL}?reset={reset.token}"
        html = f"""
            <p>Pediste restablecer tu contraseña en Qué Puedo Cursar.</p>
            <p><a href="{link}">Hacé clic acá para elegir una contraseña nueva</a></p>
            <p>El link vence en 1 hora. Si no fuiste vos, ignorá este mail.</p>
        """
        background_tasks.add_task(enviar_email, usuario.email, "Restablecer tu contraseña", html)

    return {"detail": "Si el email existe, te mandamos un link para restablecer la contraseña."}


@router.post("/reset-password", status_code=204)
def resetear_password(datos: schemas.ResetPasswordRequest, db: Session = Depends(get_db)):
    """Cambia la contraseña usando un token recibido por mail (un solo uso)."""
    if len(datos.password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

    reset = db.query(models.PasswordResetToken).filter(models.PasswordResetToken.token == datos.token).first()
    if not reset or reset.usado or reset.expira_en < datetime.utcnow():
        raise HTTPException(status_code=400, detail="El link para restablecer la contraseña es inválido o venció")

    usuario = db.query(models.Usuario).filter(models.Usuario.id == reset.usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    usuario.password_hash = auth.hash_password(datos.password)
    reset.usado = True
    # Cerrar todas las sesiones activas: si alguien más tenía la cuenta abierta
    # (o comprometida), queda afuera al cambiar la contraseña.
    db.query(models.Sesion).filter(models.Sesion.usuario_id == usuario.id).delete()
    db.commit()

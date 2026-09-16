"""
Script CLI para elevar un usuario existente a rol ADMIN.

Uso:
    python scripts/make_admin.py EMAIL

Ejemplo:
    python scripts/make_admin.py l390585@gmail.com

Ejecutar desde la raiz del proyecto (donde esta app/) para que los
imports de app.* funcionen correctamente.
"""
import sys
from pathlib import Path

# Agregar la raiz del proyecto al path para poder importar app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models
from app.auth import normalizar_email
from app.database import SessionLocal


def make_admin(email):
    email = normalizar_email(email)
    db = SessionLocal()
    try:
        usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()
        if not usuario:
            print("[ERROR] No existe ningun usuario con el email: " + email)
            sys.exit(1)

        if usuario.rol == models.RolEnum.ADMIN:
            print("[INFO] " + email + " ya tiene rol ADMIN, no se hizo ningun cambio.")
            return

        usuario.rol = models.RolEnum.ADMIN
        db.commit()
        print("[OK] " + email + " ahora tiene rol ADMIN.")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python scripts/make_admin.py EMAIL")
        sys.exit(1)
    make_admin(sys.argv[1])

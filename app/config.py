"""
Carga variables de entorno desde un archivo .env en la raíz del proyecto
(si existe), sin depender de python-dotenv. Parser deliberadamente simple:
líneas KEY=VALUE, ignora comentarios (#) y líneas vacías. No pisa variables
que ya estén seteadas en el entorno real (esas tienen prioridad).
"""
import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _cargar_env():
    if not _ENV_PATH.exists():
        return
    for linea in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        os.environ.setdefault(clave, valor)


_cargar_env()

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
MAIL_FROM = os.environ.get("MAIL_FROM", "Qué Puedo Cursar <no-reply@takana.online>")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://app.takana.online")

# Bot de Telegram (ver app/telegram_api.py y app/telegram_bot.py). Sin token
# o sin chat id, el bot simplemente no arranca y los avisos no se envían.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# Cuenta de Qué Puedo Cursar a la que responde el bot (/hoy, /cursar...).
TELEGRAM_USUARIO_EMAIL = os.environ.get("TELEGRAM_USUARIO_EMAIL", "")
# Dominio público de la API: el bot lo consulta en /servicios (prueba de punta a punta).
API_PUBLICA_URL = os.environ.get("API_PUBLICA_URL", "https://quepuedocursar.takana.online")

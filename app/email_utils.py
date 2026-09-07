"""
Envío de mails vía la API REST de Resend (https://resend.com), usando sólo
`urllib` de la librería estándar — sin el SDK oficial `resend` ni `requests`,
para no sumar dependencias que puedan complicar la instalación en la tablet.
"""
import json
import logging
import urllib.error
import urllib.request

from app.config import MAIL_FROM, RESEND_API_KEY

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


def enviar_email(to: str, subject: str, html: str) -> bool:
    """Envía un mail. Devuelve True si Resend lo aceptó, False si falló
    (no rompe el flujo del caller: si el mail falla, sólo se loguea)."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY no configurada; no se envía el mail a %s", to)
        return False

    payload = json.dumps({
        "from": MAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }).encode("utf-8")

    req = urllib.request.Request(RESEND_URL, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {RESEND_API_KEY}")
    req.add_header("Content-Type", "application/json")
    # Cloudflare (delante de api.resend.com) bloquea el User-Agent default de
    # urllib ("Python-urllib/x.y") como si fuera un bot; con uno propio pasa.
    req.add_header("User-Agent", "QuePuedoCursar/1.0 (+https://takana.online)")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        logger.error("Resend rechazó el mail a %s: %s %s", to, e.code, detail)
        return False
    except Exception as e:
        logger.error("Error enviando mail a %s: %s", to, e)
        return False

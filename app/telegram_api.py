"""
Cliente mínimo de la API de bots de Telegram, sólo con `urllib` de la
librería estándar (igual que app/email_utils.py): en la tablet no se puede
compilar nada, así que se evita sumar `python-telegram-bot` y compañía.

Nunca loguea ni devuelve el token: viaja sólo en la URL de cada pedido, y los
mensajes de error que se registran no incluyen la URL.
"""
import json
import logging
import urllib.error
import urllib.request
from typing import List, Optional

from app import config

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{metodo}"
LIMITE_MENSAJE = 3500  # Telegram corta en 4096; se deja margen


class ConflictoPolling(Exception):
    """Otro proceso está haciendo getUpdates con el mismo token (HTTP 409).
    Pasa un rato después de reiniciar el servidor, hasta que el pedido largo
    del proceso anterior expira."""


class TokenInvalido(Exception):
    """Telegram rechazó el token (HTTP 401): reintentar no sirve."""


def esta_configurado() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def _pedir(metodo: str, datos: dict, timeout: float) -> dict:
    url = API.format(token=config.TELEGRAM_BOT_TOKEN, metodo=metodo)
    req = urllib.request.Request(url, data=json.dumps(datos).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "QuePuedoCursar/1.0 (+https://takana.online)")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # e.read() es el JSON de error de Telegram; no incluye el token.
        if e.code == 409:
            raise ConflictoPolling()
        if e.code == 401:
            raise TokenInvalido()
        raise


def enviar_mensaje(texto: str, chat_id: Optional[str] = None) -> bool:
    """Manda un mensaje de texto plano. Devuelve True si Telegram lo aceptó.
    Nunca levanta excepciones: un aviso que falla no debe romper a quien lo
    llama (el deploy, el recordatorio de las 21:00, etc.)."""
    destino = chat_id or config.TELEGRAM_CHAT_ID
    if not config.TELEGRAM_BOT_TOKEN or not destino:
        return False
    try:
        r = _pedir("sendMessage", {"chat_id": destino, "text": texto[:LIMITE_MENSAJE]}, timeout=10)
        return bool(r.get("ok"))
    except Exception as e:
        logger.warning("Telegram: no se pudo enviar el mensaje (%s: %s)", e.__class__.__name__, repr(getattr(e, "reason", ""))[:80])
        return False


def obtener_updates(offset: Optional[int], timeout: int = 30) -> List[dict]:
    """getUpdates con long polling. `offset` = id del último update + 1."""
    datos = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset is not None:
        datos["offset"] = offset
    r = _pedir("getUpdates", datos, timeout=timeout + 10)
    return r.get("result", [])

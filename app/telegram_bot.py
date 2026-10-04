"""
Bot de Telegram personal: responde comandos desde el celular del dueño
(/hoy, /manana, /cursar, /estado) con datos de Qué Puedo Cursar y de la
tablet.

Corre como un hilo en segundo plano dentro del mismo proceso de uvicorn
(arrancado en el startup de FastAPI, ver app/main.py), con long polling:
no necesita URL pública, túnel ni abrir puertos. Sin TELEGRAM_BOT_TOKEN y
TELEGRAM_CHAT_ID no arranca.

Seguridad: sólo se le hace caso al chat de TELEGRAM_CHAT_ID. Cualquier otro
mensaje se ignora sin responder (para que un desconocido no sepa ni que el
bot está vivo).
"""
import logging
import os
import shutil
import subprocess
import threading
import time
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import func

from app import config, models, telegram_api
from app.database import SessionLocal
from app.routers.consultas import _estados_map, _puede_cursar
from app.scheduler import _ahora_argentina, _formatear_texto, eventos_visibles

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent.parent
ARRANQUE = time.time()
EDAD_MAXIMA_MENSAJE = 600  # un comando de hace más de 10 min ya no tiene sentido responderlo
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

AYUDA = (
    "Comandos:\n"
    "/hoy — agenda de hoy\n"
    "/manana — agenda de mañana\n"
    "/cursar — materias que podés cursar ahora\n"
    "/estado — estado de la tablet y del servidor\n"
    "/ayuda — esta lista"
)


# ─── Datos ────────────────────────────────────────────────────────────────────

def _usuario_del_bot(db) -> Optional[models.Usuario]:
    email = config.TELEGRAM_USUARIO_EMAIL.strip().lower()
    if not email:
        return None
    return db.query(models.Usuario).filter(func.lower(models.Usuario.email) == email).first()


def _agenda(db, fecha, etiqueta: str) -> str:
    usuario = _usuario_del_bot(db)
    eventos = eventos_visibles(db, fecha, usuario.id if usuario else None)
    titulo = f"Agenda de {etiqueta} ({DIAS[fecha.weekday()]} {fecha.strftime('%d/%m')})"
    if not eventos:
        return titulo + "\nNo hay nada anotado."
    return _formatear_texto(eventos, titulo)


def _cursar(db) -> str:
    usuario = _usuario_del_bot(db)
    if usuario is None:
        return "No sé de qué cuenta hablás: falta configurar TELEGRAM_USUARIO_EMAIL en el .env de la tablet."
    estados = _estados_map(db, usuario.id)
    materias = [
        m for m in db.query(models.Materia).order_by(models.Materia.anio, models.Materia.nombre).all()
        if estados.get(m.id, models.EstadoEnum.NO_CURSADA) == models.EstadoEnum.NO_CURSADA
        and _puede_cursar(m, estados)
    ]
    if not materias:
        return "Ahora mismo no tenés materias habilitadas para cursar."
    lineas = ["Podés cursar ({}):".format(len(materias))]
    for m in materias[:40]:
        anio = f" — {m.anio}º año" if m.anio else ""
        lineas.append(f"• {m.nombre}{anio}")
    if len(materias) > 40:
        lineas.append(f"… y {len(materias) - 40} más (mirá la app).")
    return "\n".join(lineas)


def _duracion(segundos: float) -> str:
    segundos = int(segundos)
    d, resto = divmod(segundos, 86400)
    h, resto = divmod(resto, 3600)
    m = resto // 60
    return (f"{d}d " if d else "") + f"{h}h {m}m"


def _proceso_vivo(archivo_pid: str) -> str:
    try:
        pid = int((RAIZ / archivo_pid).read_text().strip())
        os.kill(pid, 0)
        return "activo"
    except Exception:
        return "NO responde"


def _ram_libre() -> str:
    try:
        for linea in Path("/proc/meminfo").read_text().splitlines():
            if linea.startswith("MemAvailable:"):
                return "{} MB".format(int(linea.split()[1]) // 1024)
    except Exception:
        pass
    return "n/d"


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "log", "-1", "--format=%h %s"], cwd=str(RAIZ), timeout=5, stderr=subprocess.DEVNULL
        ).decode("utf-8", errors="replace").strip()
    except Exception:
        return "n/d"


def _ultimo_deploy() -> str:
    """Última línea 'Redeploy completo' de deploy.out (su fecha va al inicio)."""
    try:
        lineas = (RAIZ / "deploy.out").read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        for linea in reversed(lineas):
            if "Redeploy completo" in linea:
                return linea.split("]")[0].lstrip("[")
    except Exception:
        pass
    return "n/d"


def _estado(db) -> str:
    try:
        libre_gb = shutil.disk_usage(str(RAIZ)).free / (1024 ** 3)
        disco = f"{libre_gb:.1f} GB libres"
    except Exception:
        disco = "n/d"
    try:
        usuarios = db.query(func.count(models.Usuario.id)).scalar()
    except Exception:
        usuarios = "n/d"
    return "\n".join([
        "Estado del servidor",
        f"• API: arriba hace {_duracion(time.time() - ARRANQUE)}",
        f"• Túnel (cloudflared): {_proceso_vivo('.cloudflared.pid')}",
        f"• RAM libre: {_ram_libre()}",
        f"• Disco: {disco}",
        f"• Usuarios registrados: {usuarios}",
        f"• Commit: {_commit()}",
        f"• Último deploy: {_ultimo_deploy()}",
    ])


# ─── Comandos ─────────────────────────────────────────────────────────────────

def _normalizar_comando(texto: str) -> str:
    """'/Mañana@MiBot extra' -> 'manana'."""
    primero = texto.strip().split()[0].lstrip("/").split("@")[0].lower() if texto.strip() else ""
    return unicodedata.normalize("NFKD", primero).encode("ascii", "ignore").decode("ascii")


def responder(texto: str, chat_id) -> Optional[str]:
    """Texto de respuesta a un mensaje, o None si hay que ignorarlo."""
    if not config.TELEGRAM_CHAT_ID or str(chat_id) != str(config.TELEGRAM_CHAT_ID).strip():
        return None
    comando = _normalizar_comando(texto)
    if comando in ("start", "ayuda", "help"):
        return AYUDA
    if comando not in ("hoy", "manana", "cursar", "estado"):
        return "No entendí. " + AYUDA

    db = SessionLocal()
    try:
        if comando == "hoy":
            return _agenda(db, _ahora_argentina().date(), "hoy")
        if comando == "manana":
            return _agenda(db, (_ahora_argentina() + timedelta(days=1)).date(), "mañana")
        if comando == "cursar":
            return _cursar(db)
        return _estado(db)
    except Exception:
        logger.exception("Telegram: falló el comando %s", comando)
        return "Algo falló procesando ese comando. Revisá uvicorn.log en la tablet."
    finally:
        db.close()


# ─── Polling ──────────────────────────────────────────────────────────────────

def _procesar_update(update: dict) -> None:
    mensaje = update.get("message") or {}
    texto = mensaje.get("text")
    chat = mensaje.get("chat") or {}
    if not texto or "id" not in chat:
        return
    if time.time() - mensaje.get("date", 0) > EDAD_MAXIMA_MENSAJE:
        return
    respuesta = responder(texto, chat["id"])
    if respuesta:
        telegram_api.enviar_mensaje(respuesta, chat_id=chat["id"])


def _loop(detener: threading.Event) -> None:
    offset = None
    espera = 5
    while not detener.is_set():
        try:
            updates = telegram_api.obtener_updates(offset, timeout=30)
            espera = 5
        except telegram_api.ConflictoPolling:
            detener.wait(15)
            continue
        except telegram_api.TokenInvalido:
            logger.error("Telegram: el token del bot fue rechazado; el bot se detiene.")
            return
        except Exception as e:
            logger.warning("Telegram: error consultando updates (%s)", e.__class__.__name__)
            detener.wait(espera)
            espera = min(espera * 2, 300)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            try:
                _procesar_update(update)
            except Exception:
                logger.exception("Telegram: falló procesando un mensaje")


def iniciar_bot() -> Optional[threading.Thread]:
    """Arranca el hilo del bot si está configurado. Devuelve el hilo, o None."""
    if not telegram_api.esta_configurado():
        logger.info("Bot de Telegram sin configurar (falta token o chat id): no se inicia.")
        return None
    hilo = threading.Thread(target=_loop, args=(threading.Event(),), daemon=True, name="telegram-bot")
    hilo.start()
    logger.info("Bot de Telegram iniciado.")
    return hilo

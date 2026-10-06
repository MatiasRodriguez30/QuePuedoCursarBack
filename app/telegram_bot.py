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
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import List, Optional

from sqlalchemy import func

from app import config, models, sistema_info, telegram_api
from app.database import DB_PATH, SessionLocal
from app.routers.consultas import _estados_map, _puede_cursar
from app.scheduler import _ahora_argentina, _formatear_texto, _segundos_hasta_proximo_envio, eventos_visibles
from app.ws_manager import manager

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
    "/pc — el equipo: batería, temperatura, RAM, discos, Wi-Fi\n"
    "/consumo — cuánta energía gasta el equipo ahora\n"
    "/servicios — Qué Puedo Cursar: API, base, dominio, recordatorio\n"
    "/estado — todo lo anterior junto\n"
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


def _proceso_vivo(archivo_pid: str) -> Optional[str]:
    """'activo' o 'NO responde' si existe ese .pid (modo tablet); None si no hay."""
    ruta = RAIZ / archivo_pid
    if not ruta.exists():
        return None
    try:
        os.kill(int(ruta.read_text().strip()), 0)
        return "activo"
    except Exception:
        return "NO responde"


def _probar_publico() -> str:
    """Pide el dominio público de la API: prueba de punta a punta (internet →
    Cloudflare → túnel → API), que es justo lo que ven los usuarios."""
    url = config.API_PUBLICA_URL.rstrip("/") + "/openapi.json"
    inicio = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "QuePuedoCursar-bot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            ms = int((time.time() - inicio) * 1000)
            return f"OK (HTTP {resp.status}, {ms} ms)" if resp.status == 200 else f"responde con HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return f"ERROR HTTP {e.code} (530 = túnel caído)"
    except Exception as e:
        return f"NO responde ({e.__class__.__name__})"


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


def _servicios(db) -> str:
    lineas = ["Servicios de Qué Puedo Cursar"]
    lineas.append(f"• API: activa hace {sistema_info.duracion(time.time() - ARRANQUE)}")
    try:
        usuarios = db.query(func.count(models.Usuario.id)).scalar()
        try:
            tam = f", {os.path.getsize(str(DB_PATH)) / 1024 ** 2:.1f} MB"
        except Exception:
            tam = ""
        lineas.append(f"• Base de datos: OK ({usuarios} usuarios{tam})")
    except Exception as e:
        lineas.append(f"• Base de datos: ERROR ({e.__class__.__name__})")
    lineas.append(f"• Dominio público: {_probar_publico()}")
    tunel = _proceso_vivo(".cloudflared.pid")
    if tunel:
        lineas.append(f"• Túnel (cloudflared): {tunel}")
    lineas.append(f"• Recordatorio de las 21:00: programado, próximo en {sistema_info.duracion(_segundos_hasta_proximo_envio())}")
    lineas.append("• Mails (Resend): " + ("configurado" if config.RESEND_API_KEY else "SIN configurar"))
    lineas.append(f"• Conexiones en vivo: {len(manager.active_connections)} dispositivos, {len(manager.usuarios_en_linea())} usuarios")
    commit = _commit()
    if commit != "n/d":
        lineas.append(f"• Versión: {commit}")
    deploy = _ultimo_deploy()
    if deploy != "n/d":
        lineas.append(f"• Último deploy: {deploy}")
    return "\n".join(lineas)


def _equipo() -> str:
    lineas = ["El equipo"]
    b = sistema_info.bateria()
    lineas.append("• Batería: " + (sistema_info.describir_bateria(b) if b else "este equipo no tiene (o no se puede leer)"))
    t = sistema_info.temperatura_cpu()
    if t is not None:
        lineas.append(f"• Temperatura CPU: {t:.0f} °C" + (" (caliente)" if t >= 85 else ""))
    rpm = sistema_info.ventilador_rpm()
    if rpm is not None:
        lineas.append(f"• Ventilador: {rpm} rpm" + (" (parado)" if rpm == 0 else ""))
    c = sistema_info.carga()
    if c:
        lineas.append(f"• Carga: {c[0]:.2f} / {c[1]:.2f} / {c[2]:.2f} (1, 5 y 15 min; {os.cpu_count() or 1} núcleos)")
    m = sistema_info.memoria()
    if m:
        lineas.append(f"• RAM: {m[1]} MB libres de {m[0]} MB")
    for nombre, libre, total in sistema_info.discos([("Disco del sistema", "/"), ("Disco de datos", "/data")]):
        lineas.append(f"• {nombre}: {libre:.1f} GB libres de {total:.0f} GB")
    w = sistema_info.wifi()
    if w:
        lineas.append(f"• Wi-Fi ({w[0]}): {w[1]} dBm, señal {sistema_info.calidad_wifi(w[1])}")
    lineas.extend(sistema_info.describir_consumo())
    up = sistema_info.uptime_segundos()
    if up is not None:
        lineas.append(f"• Encendido hace: {sistema_info.duracion(up)}")
    return "\n".join(lineas)


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
    if comando not in ("hoy", "manana", "cursar", "estado", "pc", "servicios", "consumo"):
        return "No entendí. " + AYUDA

    db = SessionLocal()
    try:
        if comando == "hoy":
            return _agenda(db, _ahora_argentina().date(), "hoy")
        if comando == "manana":
            return _agenda(db, (_ahora_argentina() + timedelta(days=1)).date(), "mañana")
        if comando == "cursar":
            return _cursar(db)
        if comando == "pc":
            return _equipo()
        if comando == "consumo":
            lineas = sistema_info.describir_consumo()
            return "\n".join(["Consumo del equipo"] + lineas) if lineas else "Este equipo no informa su consumo."
        if comando == "servicios":
            return _servicios(db)
        return _servicios(db) + "\n\n" + _equipo()
    except Exception:
        logger.exception("Telegram: falló el comando %s", comando)
        return "Algo falló procesando ese comando. Revisá los logs del servidor."
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
            logger.warning("Telegram: error consultando updates (%s: %s)", e.__class__.__name__, repr(getattr(e, "reason", ""))[:80])
            detener.wait(espera)
            espera = min(espera * 2, 300)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            try:
                _procesar_update(update)
            except Exception:
                logger.exception("Telegram: falló procesando un mensaje")


def _procesar_energia(estado: dict, b: Optional[dict], umbral_bajo: int = 20) -> List[str]:
    """Una lectura de la batería -> avisos a mandar. `estado` guarda lo que se
    recuerda entre lecturas. El primer estado se fija sin avisar, y un cambio
    sólo se confirma si aparece en dos lecturas seguidas (así un parpadeo del
    sensor no dispara una falsa alarma de corte de luz)."""
    if b is None:
        return []
    ahora = b.get("estado") == "Discharging"
    pct = b.get("porcentaje")
    avisos: List[str] = []
    if estado.get("confirmado") is None:
        estado["confirmado"] = ahora
    elif ahora != estado["confirmado"]:
        if estado.get("candidato") == ahora:
            estado["confirmado"], avisos = sistema_info.evaluar_energia(estado["confirmado"], b)
            estado["candidato"] = None
            estado["bajo_avisado"] = False
        else:
            estado["candidato"] = ahora
    else:
        estado["candidato"] = None
    if estado["confirmado"] and pct is not None and pct <= umbral_bajo and not estado.get("bajo_avisado"):
        avisos.append(f"Batería baja: {pct}%. Si no vuelve la corriente, el servidor se apaga pronto.")
        estado["bajo_avisado"] = True
    return avisos


def _vigilar_energia(detener: threading.Event, intervalo: int = 30) -> None:
    """Avisa por Telegram cuando se corta o vuelve la luz y cuando la batería
    queda baja. Si el equipo no tiene batería no hace nada."""
    if sistema_info.bateria() is None:
        return
    estado: dict = {}
    while True:
        try:
            for aviso in _procesar_energia(estado, sistema_info.bateria()):
                telegram_api.enviar_mensaje(aviso)
        except Exception:
            logger.exception("Telegram: falló la vigilancia de energía")
        if detener.wait(intervalo):
            return


def _procesar_temperatura(estado: dict, temp: Optional[float], umbral: int = 90, normal: int = 80, lecturas: int = 5) -> List[str]:
    """Una lectura de la temperatura de la CPU -> avisos a mandar. Sólo avisa si
    se mantiene >= `umbral` durante `lecturas` lecturas seguidas (con una cada
    30 s son ~2 minutos): un pico corto es normal, por ejemplo al empezar una
    carga el firmware deja subir la CPU unos 27 s antes de aplicar el límite de
    potencia. Avisa una vez por episodio, y una vez más cuando baja de `normal`."""
    if temp is None:
        return []
    avisos: List[str] = []
    if temp >= umbral:
        estado["calientes"] = estado.get("calientes", 0) + 1
        if estado["calientes"] >= lecturas and not estado.get("avisado"):
            avisos.append(f"CPU caliente: {temp:.0f} °C sostenidos por más de 2 minutos. Revisá la ventilación y qué está usando el equipo (/pc).")
            estado["avisado"] = True
    else:
        estado["calientes"] = 0
        if estado.get("avisado") and temp < normal:
            avisos.append(f"La temperatura de la CPU volvió a la normalidad ({temp:.0f} °C).")
            estado["avisado"] = False
    return avisos


def _vigilar_temperatura(detener: threading.Event, intervalo: int = 30) -> None:
    """Avisa por Telegram si la CPU se queda caliente de forma sostenida. Si el
    equipo no expone la temperatura no hace nada."""
    if sistema_info.temperatura_cpu() is None:
        return
    estado: dict = {}
    while True:
        try:
            for aviso in _procesar_temperatura(estado, sistema_info.temperatura_cpu()):
                telegram_api.enviar_mensaje(aviso)
        except Exception:
            logger.exception("Telegram: falló la vigilancia de temperatura")
        if detener.wait(intervalo):
            return


def iniciar_bot() -> Optional[threading.Thread]:
    """Arranca el hilo del bot si está configurado. Devuelve el hilo, o None."""
    if not telegram_api.esta_configurado():
        logger.info("Bot de Telegram sin configurar (falta token o chat id): no se inicia.")
        return None
    detener = threading.Event()
    hilo = threading.Thread(target=_loop, args=(detener,), daemon=True, name="telegram-bot")
    hilo.start()
    threading.Thread(target=_vigilar_energia, args=(detener,), daemon=True, name="telegram-energia").start()
    threading.Thread(target=_vigilar_temperatura, args=(detener,), daemon=True, name="telegram-temperatura").start()
    logger.info("Bot de Telegram iniciado.")
    return hilo

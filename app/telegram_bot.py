"""
Bot de Telegram personal: responde comandos desde el celular del dueño con
datos de sus servicios, agrupados en /servicios (con botones): Qué Puedo
Cursar, el servidor de Minecraft y el POS Takana. Es también el ÚNICO lugar
donde se dan de alta los usuarios del POS.

Corre como un hilo en segundo plano dentro del mismo proceso de uvicorn
(arrancado en el startup de FastAPI, ver app/main.py), con long polling:
no necesita URL pública, túnel ni abrir puertos. Sin TELEGRAM_BOT_TOKEN y
TELEGRAM_CHAT_ID no arranca.

Seguridad: sólo se le hace caso al chat de TELEGRAM_CHAT_ID. Cualquier otro
mensaje se ignora sin responder (para que un desconocido no sepa ni que el
bot está vivo).
"""
import ipaddress
import json
import logging
import os
import re
import ssl
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    "/servicios — elegí un servicio con botones\n"
    "\n"
    "Qué Puedo Cursar — /qpc\n"
    "/hoy — agenda de hoy\n"
    "/manana — agenda de mañana\n"
    "/cursar — materias que podés cursar ahora\n"
    "/qpcestado — API, base, dominio y recordatorio\n"
    "\n"
    "Servidor de Minecraft — /mc\n"
    "/panelservidormc — panel de administración (link)\n"
    "/token — código para entrar al panel\n"
    "\n"
    "POS Takana — /pos\n"
    "/posalta — dar de alta un usuario (el único lugar donde se crean)\n"
    "/posusuarios — usuarios: nueva contraseña o borrar\n"
    "/posestado — API y usuarios\n"
    "\n"
    "El equipo\n"
    "/pc — batería, temperatura, RAM, discos, Wi-Fi\n"
    "/consumo — cuánta energía gasta ahora\n"
    "/estado — Qué Puedo Cursar + el equipo\n"
    "/cancelar — corta un alta a medio hacer\n"
    "/ayuda — esta lista"
)

# Filas de botones: (texto, dato). El dato vuelve en el callback_query al tocarlo.
Botones = List[List[Tuple[str, str]]]
Respuesta = Tuple[str, Optional[Botones]]

# Lo que el bot espera como próximo mensaje de texto, por chat (por ahora sólo
# el nombre de un alta del POS). En memoria: un reinicio corta el alta y listo.
_esperando: Dict[str, str] = {}
USUARIO_POS = re.compile(r"[a-z0-9._-]{3,30}")  # la misma regla que valida el POS
FALTA_TOKEN_POS = "Falta POS_INTERNO_TOKEN en el .env del bot: sin eso no puedo tocar los usuarios del POS."
ROLES_POS = (
    ("admin", "Admin: todo, incluidos productos y precios"),
    ("vendedor", "Vendedor: cobrar y ver ventas"),
    ("invitado", "Invitado: sólo consultar precios"),
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
    return _probar_url(config.API_PUBLICA_URL.rstrip("/") + "/openapi.json")


def _probar_url(url: str) -> str:
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


# ─── Panel del servidor de Minecraft ──────────────────────────────────────────

def _llamar_panel(ruta: str, cuerpo: Optional[dict] = None, timeout: int = 6):
    """Habla con el panel del servidor de Minecraft (red de casa). Devuelve (código HTTP, JSON).

    El panel tiene un certificado autofirmado, así que no se valida; por eso solo se le habla a una IP privada
    (red de casa o Tailscale) y solo se le manda un nombre de usuario (el código llega por Telegram, no por acá).
    Levanta OSError/ValueError si no se puede.
    """
    partes = urllib.parse.urlsplit(config.PANEL_MC_URL)
    if not partes.hostname or not ipaddress.ip_address(partes.hostname).is_private:
        raise ValueError("PANEL_MC_URL tiene que ser una IP privada (red de casa)")
    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(
        config.PANEL_MC_URL.rstrip("/") + ruta, data=datos, method="POST" if datos is not None else "GET",
        headers={"Content-Type": "application/json", "X-Panel": "1", "User-Agent": "QuePuedoCursar-bot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=contexto) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def _probar_panel_mc() -> str:
    try:
        codigo, _ = _llamar_panel("/api/session", timeout=4)
        return "disponible" if codigo == 200 else f"responde con HTTP {codigo}"
    except Exception as e:
        return f"NO responde ({e.__class__.__name__})"


def _panel_mc() -> str:
    lineas = ["Panel de administración del servidor de Minecraft", "Link: " + config.PANEL_MC_URL]
    if config.PANEL_MC_URL_TAILSCALE:
        lineas.append("Por Tailscale: " + config.PANEL_MC_URL_TAILSCALE)
    lineas += [
        f"Usuario: {config.PANEL_MC_USUARIO}",
        "Solo se abre desde la red de casa o por Tailscale (no está en internet). El navegador avisa por el certificado: aceptalo una vez.",
        "/token — te mando el código para entrar",
    ]
    return "\n".join(lineas)


def _token_panel() -> str:
    try:
        codigo, datos = _llamar_panel("/api/telegram/request", {"usuario": config.PANEL_MC_USUARIO})
    except Exception as e:
        logger.warning("Telegram: no se pudo pedir el código del panel (%s)", e.__class__.__name__)
        return "No pude hablar con el panel del servidor de Minecraft (¿está apagado o fuera de la red de casa?)."
    if codigo == 200:
        return (f"Pedí el código de acceso para {config.PANEL_MC_USUARIO}: te llega en otro mensaje y vence en 5 minutos. "
                "Escribilo en la pantalla de ingreso del panel (/panelservidormc tiene el link).")
    if codigo == 429:
        return "Pediste demasiados códigos seguidos. Esperá unos minutos y probá de nuevo."
    if codigo == 409:
        return "El panel todavía no tiene el acceso por Telegram configurado (scripts/panel-telegram.sh en el servidor)."
    return f"El panel respondió con un error (HTTP {codigo})."


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


# ─── POS Takana ───────────────────────────────────────────────────────────────

def _llamar_pos(metodo: str, ruta: str, cuerpo: Optional[dict] = None, timeout: int = 8):
    """Habla con las rutas internas del POS (mismo servidor). Devuelve (código HTTP, JSON).

    Sólo a una dirección local: el token interno nunca sale del equipo. Levanta
    OSError/ValueError si no se puede.
    """
    if urllib.parse.urlsplit(config.POS_API_URL).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("POS_API_URL tiene que ser local (127.0.0.1)")
    req = urllib.request.Request(
        config.POS_API_URL.rstrip("/") + ruta, method=metodo,
        data=json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None,
        headers={"Content-Type": "application/json", "X-Interno-Token": config.POS_INTERNO_TOKEN,
                 "User-Agent": "QuePuedoCursar-bot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def _pedir_pos(metodo: str, ruta: str, cuerpo: Optional[dict] = None):
    """(datos, None) si salió bien, o (None, texto del error para el chat)."""
    if not config.POS_INTERNO_TOKEN:
        return None, FALTA_TOKEN_POS
    try:
        codigo, datos = _llamar_pos(metodo, ruta, cuerpo)
    except Exception as e:
        logger.warning("Telegram: no se pudo hablar con el POS (%s)", e.__class__.__name__)
        return None, "No pude hablar con el POS (¿está levantado el contenedor takanapos?)."
    if 200 <= codigo < 300:
        return datos, None
    if codigo in (400, 409) and isinstance(datos, dict) and datos.get("detail"):
        return None, str(datos["detail"])
    if codigo == 404 and ruta == "/api/interno/usuarios":
        return None, "El POS rechazó el token interno: revisá que POS_INTERNO_TOKEN sea igual al INTERNO_TOKEN del POS."
    if codigo == 404:
        return None, "Ese usuario no existe."
    return None, f"El POS respondió con un error (HTTP {codigo})."


def _pos_estado() -> str:
    lineas = ["POS Takana", "• Web: https://pos.takana.online",
              f"• API pública: {_probar_url(config.POS_PUBLICA_URL.rstrip('/') + '/openapi.json')}"]
    usuarios, error = _pedir_pos("GET", "/api/interno/usuarios")
    lineas.append(f"• Usuarios: {len(usuarios)}" if error is None else "• Usuarios: " + error)
    return "\n".join(lineas)


def _pos_usuarios() -> Respuesta:
    usuarios, error = _pedir_pos("GET", "/api/interno/usuarios")
    if error:
        return error, None
    alta = [("Dar de alta usuario", "pos:alta")]
    if not usuarios:
        return "El POS no tiene usuarios todavía.", [alta]
    texto = "Usuarios del POS:\n" + "\n".join(f"• {u['username']} ({u['rol']})" for u in usuarios)
    botones = [[("Nueva contraseña: " + u["username"], "pos:clave:" + u["username"]),
                ("Borrar: " + u["username"], "pos:borrar:" + u["username"])] for u in usuarios]
    return texto, botones + [alta]


def _pos_alta_inicio(chat_id) -> Respuesta:
    if not config.POS_INTERNO_TOKEN:
        return FALTA_TOKEN_POS, None
    _esperando[str(chat_id)] = "pos_alta"
    return ("Alta de usuario del POS. Escribí el nombre de usuario: de 3 a 30 letras minúsculas, "
            "números, punto o guion, sin espacios. /cancelar para salir."), None


def _pos_alta_pedir_rol(nombre: str) -> Respuesta:
    nombre = nombre.strip().lower()
    if not USUARIO_POS.fullmatch(nombre):
        return ("Ese nombre no sirve: de 3 a 30 letras minúsculas, números, punto o guion, sin espacios. "
                "Tocá de nuevo para reintentar."), [[("Dar de alta usuario", "pos:alta")]]
    botones = [[(etiqueta, f"pos:rol:{rol}:{nombre}")] for rol, etiqueta in ROLES_POS]
    return f"¿Qué rol tiene {nombre}?", botones + [[("Cancelar", "cmd:cancelar")]]


def _pos_crear(nombre: str, rol: str) -> str:
    datos, error = _pedir_pos("POST", "/api/interno/usuarios", {"username": nombre, "rol": rol})
    if error:
        return "No se creó: " + error
    return (f"Usuario creado en el POS\nUsuario: {datos['username']}\nRol: {datos['rol']}\n"
            f"Contraseña: {datos['contrasena']}\n\n"
            "Entrá en https://pos.takana.online o en la app. La contraseña no se vuelve a mostrar: "
            "si se pierde, generá otra desde /posusuarios. Conviene borrar este mensaje después de anotarla.")


def _pos_nueva_clave(nombre: str) -> str:
    datos, error = _pedir_pos("POST", f"/api/interno/usuarios/{urllib.parse.quote(nombre)}/contrasena")
    if error:
        return "No se cambió: " + error
    return (f"Contraseña nueva para {datos['username']}: {datos['contrasena']}\n"
            "La anterior ya no funciona. Conviene borrar este mensaje después de anotarla.")


def _pos_borrar(nombre: str) -> str:
    _, error = _pedir_pos("DELETE", f"/api/interno/usuarios/{urllib.parse.quote(nombre)}")
    if error:
        return "No se borró: " + error
    return f"Usuario {nombre} borrado del POS."


def _boton_pos(accion: str, chat_id) -> Optional[Respuesta]:
    tipo, _, arg = accion.partition(":")
    if tipo == "alta":
        return _pos_alta_inicio(chat_id)
    if tipo == "rol":
        rol, _, nombre = arg.partition(":")
        if rol in dict(ROLES_POS) and USUARIO_POS.fullmatch(nombre):
            return _pos_crear(nombre, rol), None
        return None
    if tipo == "clave":
        return (f"¿Generar una contraseña nueva para {arg}? La actual deja de funcionar.",
                [[("Sí, generar", "pos:clave!:" + arg), ("Cancelar", "cmd:cancelar")]])
    if tipo == "clave!":
        return _pos_nueva_clave(arg), None
    if tipo == "borrar":
        return (f"¿Borrar el usuario {arg} del POS? No se puede deshacer.",
                [[("Sí, borrar", "pos:borrar!:" + arg), ("Cancelar", "cmd:cancelar")]])
    if tipo == "borrar!":
        return _pos_borrar(arg), None
    return None


# ─── Menús ────────────────────────────────────────────────────────────────────

def _menu_servicios() -> Respuesta:
    return "¿Qué servicio?", [
        [("Qué Puedo Cursar", "cmd:qpc")],
        [("Servidor de Minecraft", "cmd:mc")],
        [("POS Takana", "cmd:pos")],
    ]


def _menu_qpc() -> Respuesta:
    return "Qué Puedo Cursar", [
        [("Agenda de hoy", "cmd:hoy"), ("Agenda de mañana", "cmd:manana")],
        [("Materias para cursar", "cmd:cursar")],
        [("Estado", "cmd:qpcestado")],
    ]


def _menu_mc() -> Respuesta:
    return f"Servidor de Minecraft\nPanel: {_probar_panel_mc()}\n/panelservidormc — link\n/token — código de acceso", [
        [("Link del panel", "cmd:panelservidormc"), ("Código de acceso", "cmd:token")],
    ]


def _menu_pos() -> Respuesta:
    return "POS Takana\nLos usuarios del POS se dan de alta sólo desde acá.", [
        [("Dar de alta usuario", "pos:alta")],
        [("Usuarios", "cmd:posusuarios"), ("Estado", "cmd:posestado")],
    ]


# ─── Comandos ─────────────────────────────────────────────────────────────────

def _normalizar_comando(texto: str) -> str:
    """'/Mañana@MiBot extra' -> 'manana'."""
    primero = texto.strip().split()[0].lstrip("/").split("@")[0].lower() if texto.strip() else ""
    return unicodedata.normalize("NFKD", primero).encode("ascii", "ignore").decode("ascii")


def _es_del_dueno(chat_id) -> bool:
    return bool(config.TELEGRAM_CHAT_ID) and str(chat_id) == str(config.TELEGRAM_CHAT_ID).strip()


def responder(texto: str, chat_id) -> Optional[str]:
    """Texto de respuesta a un mensaje, o None si hay que ignorarlo."""
    r = responder_completo(texto, chat_id)
    return r[0] if r else None


def responder_completo(texto: str, chat_id) -> Optional[Respuesta]:
    """(texto, botones) de respuesta a un mensaje, o None si hay que ignorarlo."""
    if not _es_del_dueno(chat_id):
        return None
    esperando = _esperando.pop(str(chat_id), None)
    if esperando == "pos_alta" and not texto.strip().startswith("/"):
        return _pos_alta_pedir_rol(texto)
    return _ejecutar(_normalizar_comando(texto), chat_id)


def responder_boton(dato: str, chat_id) -> Optional[Respuesta]:
    """(texto, botones) al tocar un botón, o None si hay que ignorarlo."""
    if not _es_del_dueno(chat_id):
        return None
    _esperando.pop(str(chat_id), None)
    tipo, _, resto = dato.partition(":")
    if tipo == "cmd":
        return _ejecutar(resto, chat_id)
    if tipo == "pos":
        return _boton_pos(resto, chat_id)
    return None


COMANDOS_CON_BASE = ("hoy", "manana", "cursar", "estado", "pc", "consumo", "qpcestado")


def _ejecutar(comando: str, chat_id) -> Respuesta:
    if comando in ("start", "ayuda", "help"):
        return AYUDA, None
    if comando == "cancelar":
        return "Listo, cancelado.", None
    menus = {"servicios": _menu_servicios, "qpc": _menu_qpc, "mc": _menu_mc, "pos": _menu_pos,
             "posusuarios": _pos_usuarios}
    if comando in menus:
        return menus[comando]()
    if comando == "posalta":
        return _pos_alta_inicio(chat_id)
    simples = {"panelservidormc": _panel_mc, "token": _token_panel, "posestado": _pos_estado}
    if comando in simples:
        return simples[comando](), None
    if comando not in COMANDOS_CON_BASE:
        return "No entendí.\n\n" + AYUDA, None

    db = SessionLocal()
    try:
        if comando == "hoy":
            return _agenda(db, _ahora_argentina().date(), "hoy"), None
        if comando == "manana":
            return _agenda(db, (_ahora_argentina() + timedelta(days=1)).date(), "mañana"), None
        if comando == "cursar":
            return _cursar(db), None
        if comando == "pc":
            return _equipo(), None
        if comando == "consumo":
            lineas = sistema_info.describir_consumo()
            return ("\n".join(["Consumo del equipo"] + lineas) if lineas else "Este equipo no informa su consumo."), None
        if comando == "qpcestado":
            return _servicios(db), None
        return _servicios(db) + "\n\n" + _equipo(), None
    except Exception:
        logger.exception("Telegram: falló el comando %s", comando)
        return "Algo falló procesando ese comando. Revisá los logs del servidor.", None
    finally:
        db.close()


# ─── Polling ──────────────────────────────────────────────────────────────────

def _enviar(chat_id, respuesta: Respuesta) -> None:
    texto, botones = respuesta
    if botones:
        telegram_api.enviar_mensaje(texto, chat_id=chat_id, botones=botones)
    else:
        telegram_api.enviar_mensaje(texto, chat_id=chat_id)


def _procesar_update(update: dict) -> None:
    boton = update.get("callback_query")
    if boton:
        _procesar_boton(boton)
        return
    mensaje = update.get("message") or {}
    texto = mensaje.get("text")
    chat = mensaje.get("chat") or {}
    if not texto or "id" not in chat:
        return
    if time.time() - mensaje.get("date", 0) > EDAD_MAXIMA_MENSAJE:
        return
    respuesta = responder_completo(texto, chat["id"])
    if respuesta:
        _enviar(chat["id"], respuesta)


def _procesar_boton(boton: dict) -> None:
    chat = (boton.get("message") or {}).get("chat") or {}
    if "id" not in chat or not _es_del_dueno(chat["id"]):
        return  # de un chat ajeno: ni se contesta
    telegram_api.responder_boton(boton.get("id", ""))
    respuesta = responder_boton(boton.get("data") or "", chat["id"])
    if respuesta:
        _enviar(chat["id"], respuesta)


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

"""
test_telegram.py — Bot de Telegram: cliente de la API, comandos, seguridad
(sólo el chat del dueño), privacidad de la agenda y recordatorio de las 21:00.

Nada de esto toca la red: urlopen y el envío se reemplazan por falsos.
"""
import io
import json
import logging
import ssl
import urllib.error
from datetime import date, datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app import config, models, scheduler, telegram_api, telegram_bot

TOKEN = "123456:TOKEN-SECRETO-DE-PRUEBA"
CHAT = "555"


@pytest.fixture
def configurado(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.setattr(config, "TELEGRAM_USUARIO_EMAIL", "dueno@mail.com")


@pytest.fixture
def bot_db(db_session, monkeypatch):
    """El bot abre su propia sesión (SessionLocal): apuntarla a la base en memoria."""
    fabrica = sessionmaker(autocommit=False, autoflush=False, bind=db_session.bind)
    monkeypatch.setattr(telegram_bot, "SessionLocal", fabrica)
    monkeypatch.setattr(telegram_bot, "_ahora_argentina", lambda: datetime(2026, 10, 6, 10, 0))
    return db_session


def _usuario(db, email):
    u = models.Usuario(email=email, password_hash="x", rol=models.RolEnum.USER)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _evento(db, titulo, fecha, personal=False, creado_por_id=None):
    db.add(models.Evento(titulo=titulo, fecha=fecha, personal=personal, creado_por_id=creado_por_id))
    db.commit()


class _RespuestaFalsa:
    def __init__(self, cuerpo):
        self._cuerpo = json.dumps(cuerpo).encode("utf-8")
        self.status = 200

    def read(self):
        return self._cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ─── Cliente de la API ────────────────────────────────────────────────────────

def test_enviar_mensaje_sin_configurar_no_toca_la_red(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")

    def no_deberia_llamarse(*a, **k):
        raise AssertionError("no debería abrir conexiones")

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", no_deberia_llamarse)
    assert telegram_api.enviar_mensaje("hola") is False


def test_enviar_mensaje_arma_el_pedido_correcto(configurado, monkeypatch):
    vistos = {}

    def falso(req, timeout):
        vistos["url"] = req.full_url
        vistos["datos"] = json.loads(req.data.decode("utf-8"))
        return _RespuestaFalsa({"ok": True})

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", falso)
    assert telegram_api.enviar_mensaje("hola") is True
    assert vistos["url"] == "https://api.telegram.org/bot{}/sendMessage".format(TOKEN)
    assert vistos["datos"] == {"chat_id": CHAT, "text": "hola"}


def test_error_de_red_no_levanta_ni_filtra_el_token(configurado, monkeypatch, caplog):
    def roto(req, timeout):
        raise urllib.error.URLError("sin red")

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", roto)
    with caplog.at_level(logging.DEBUG):
        assert telegram_api.enviar_mensaje("hola") is False
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("codigo,excepcion", [(409, telegram_api.ConflictoPolling), (401, telegram_api.TokenInvalido)])
def test_getupdates_distingue_409_y_401(configurado, monkeypatch, codigo, excepcion):
    def con_error(req, timeout):
        raise urllib.error.HTTPError(req.full_url, codigo, "x", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", con_error)
    with pytest.raises(excepcion):
        telegram_api.obtener_updates(None, timeout=1)


# ─── Seguridad: sólo el chat del dueño ────────────────────────────────────────

def test_chat_ajeno_se_ignora_sin_responder(configurado, bot_db):
    assert telegram_bot.responder("/hoy", "999") is None
    assert telegram_bot.responder("/estado", "999") is None


def test_sin_chat_id_configurado_no_responde_a_nadie(monkeypatch, bot_db):
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert telegram_bot.responder("/hoy", "555") is None


def test_ayuda_y_comando_desconocido(configurado, bot_db):
    assert "/hoy" in telegram_bot.responder("/ayuda", CHAT)
    assert telegram_bot.responder("/start", CHAT) == telegram_bot.AYUDA
    assert telegram_bot.responder("/lo-que-sea", CHAT).startswith("No entendí")


# ─── Agenda: institucionales + lo propio, nunca lo personal de otro ───────────

def test_hoy_muestra_institucionales_y_personales_propios_pero_no_ajenos(configurado, bot_db):
    dueno = _usuario(bot_db, "dueno@mail.com")
    otro = _usuario(bot_db, "otro@mail.com")
    hoy = date(2026, 10, 6)
    _evento(bot_db, "Mesa de examen", hoy)
    _evento(bot_db, "Mi parcial", hoy, personal=True, creado_por_id=dueno.id)
    _evento(bot_db, "Secreto de otro", hoy, personal=True, creado_por_id=otro.id)
    _evento(bot_db, "Evento de mañana", date(2026, 10, 7))

    texto = telegram_bot.responder("/hoy", CHAT)
    assert "Mesa de examen" in texto
    assert "Mi parcial" in texto
    assert "Secreto de otro" not in texto
    assert "Evento de mañana" not in texto


def test_manana_acepta_la_eñe_y_el_mencion_al_bot(configurado, bot_db):
    _usuario(bot_db, "dueno@mail.com")
    _evento(bot_db, "Entrega TP", date(2026, 10, 7))
    for variante in ("/manana", "/mañana", "/Mañana@MiBot"):
        assert "Entrega TP" in telegram_bot.responder(variante, CHAT)


def test_dia_sin_eventos(configurado, bot_db):
    _usuario(bot_db, "dueno@mail.com")
    assert "No hay nada anotado" in telegram_bot.responder("/hoy", CHAT)


# ─── /cursar y /estado ────────────────────────────────────────────────────────

def test_cursar_lista_solo_lo_habilitado(configurado, bot_db):
    _usuario(bot_db, "dueno@mail.com")
    a = models.Materia(nombre="Análisis I", codigo="A1", anio=1)
    b = models.Materia(nombre="Análisis II", codigo="A2", anio=2)
    bot_db.add_all([a, b])
    bot_db.commit()
    bot_db.add(models.Prerequisito(materia_id=b.id, materia_requerida_id=a.id, tipo=models.TipoPrerequisito.REGULARIZADA))
    bot_db.commit()

    texto = telegram_bot.responder("/cursar", CHAT)
    assert "Análisis I" in texto
    assert "Análisis II" not in texto  # le falta regularizar la anterior


def test_cursar_sin_usuario_configurado_explica_que_falta(configurado, bot_db, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_USUARIO_EMAIL", "")
    assert "TELEGRAM_USUARIO_EMAIL" in telegram_bot.responder("/cursar", CHAT)


def test_estado_incluye_las_secciones_y_no_muestra_secretos(configurado, bot_db, monkeypatch):
    _usuario(bot_db, "dueno@mail.com")
    monkeypatch.setattr(telegram_bot, "_probar_publico", lambda: "OK (HTTP 200, 90 ms)")  # sin red
    monkeypatch.setattr(telegram_bot, "_probar_panel_mc", lambda: "disponible")  # sin red
    texto = telegram_bot.responder("/estado", CHAT)
    for parte in ("Servicios de Qué Puedo Cursar", "API:", "Base de datos", "Dominio público", "El equipo"):
        assert parte in texto
    assert TOKEN not in texto


# ─── Polling ──────────────────────────────────────────────────────────────────

def test_mensaje_viejo_se_ignora_y_el_reciente_se_responde(configurado, bot_db, monkeypatch):
    _usuario(bot_db, "dueno@mail.com")
    enviados = []
    monkeypatch.setattr(telegram_api, "enviar_mensaje", lambda texto, chat_id=None: enviados.append((chat_id, texto)) or True)
    ahora = telegram_bot.time.time()

    telegram_bot._procesar_update({"update_id": 1, "message": {"text": "/hoy", "chat": {"id": 555}, "date": ahora - 3600}})
    assert enviados == []

    telegram_bot._procesar_update({"update_id": 2, "message": {"text": "/hoy", "chat": {"id": 555}, "date": ahora - 5}})
    assert len(enviados) == 1 and enviados[0][0] == 555


def test_iniciar_bot_no_hace_nada_sin_configurar(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert telegram_bot.iniciar_bot() is None


def test_iniciar_bot_arranca_un_hilo_daemon_si_esta_configurado(configurado, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_loop", lambda detener: None)  # sin red
    monkeypatch.setattr(telegram_bot, "_vigilar_energia", lambda detener: None)
    monkeypatch.setattr(telegram_bot, "_vigilar_temperatura", lambda detener: None)
    hilo = telegram_bot.iniciar_bot()
    assert hilo is not None and hilo.daemon
    hilo.join(timeout=2)


# ─── Recordatorio de las 21:00 por Telegram ───────────────────────────────────

def test_recordatorio_por_telegram_solo_al_dueno_y_solo_con_lo_suyo(configurado, db_session, monkeypatch):
    fabrica = sessionmaker(autocommit=False, autoflush=False, bind=db_session.bind)
    monkeypatch.setattr(scheduler, "SessionLocal", fabrica)
    monkeypatch.setattr(scheduler, "_ahora_argentina", lambda: datetime(2026, 9, 29))
    monkeypatch.setattr(scheduler, "enviar_email", lambda *a: True)

    dueno = _usuario(db_session, "dueno@mail.com")
    otro = _usuario(db_session, "otro@mail.com")
    manana = date(2026, 9, 30)
    _evento(db_session, "Mesa de examen", manana)
    _evento(db_session, "Mi oral", manana, personal=True, creado_por_id=dueno.id)
    _evento(db_session, "Secreto de otro", manana, personal=True, creado_por_id=otro.id)

    enviados = []
    monkeypatch.setattr(scheduler, "enviar_telegram", lambda texto: enviados.append(texto) or True)

    scheduler.enviar_recordatorios_del_dia_siguiente()

    assert len(enviados) == 1  # una sola vez: al dueño, no a "otro"
    assert "Mesa de examen" in enviados[0] and "Mi oral" in enviados[0]
    assert "Secreto de otro" not in enviados[0]


# ─── Panel del servidor de Minecraft: /panelservidormc y /token ───────────────

def test_ayuda_lista_los_comandos_del_panel(configurado, bot_db):
    ayuda = telegram_bot.responder("/ayuda", CHAT)
    assert "/panelservidormc" in ayuda and "/token" in ayuda


def test_panelservidormc_da_el_link_el_usuario_y_no_muestra_secretos(configurado, bot_db):
    texto = telegram_bot.responder("/panelservidormc", CHAT)
    assert config.PANEL_MC_URL in texto
    assert config.PANEL_MC_USUARIO in texto
    assert "/token" in texto
    assert TOKEN not in texto


def test_panel_y_token_ignoran_a_chats_ajenos_sin_tocar_el_panel(configurado, bot_db, monkeypatch):
    llamadas = []
    monkeypatch.setattr(telegram_bot, "_llamar_panel", lambda *a, **k: llamadas.append(a) or (200, {}))
    assert telegram_bot.responder("/panelservidormc", "999") is None
    assert telegram_bot.responder("/token", "999") is None
    assert llamadas == []


def test_token_le_pide_el_codigo_al_panel_para_el_usuario_del_panel(configurado, bot_db, monkeypatch):
    llamadas = []

    def falso(ruta, cuerpo=None, timeout=6):
        llamadas.append((ruta, cuerpo))
        return 200, {"ok": True}

    monkeypatch.setattr(telegram_bot, "_llamar_panel", falso)
    texto = telegram_bot.responder("/token", CHAT)
    assert llamadas == [("/api/telegram/request", {"usuario": config.PANEL_MC_USUARIO})]
    assert "5 minutos" in texto


@pytest.mark.parametrize("codigo,fragmento", [(429, "demasiados"), (409, "todavía no tiene"), (500, "HTTP 500")])
def test_token_explica_los_errores_del_panel(configurado, bot_db, monkeypatch, codigo, fragmento):
    monkeypatch.setattr(telegram_bot, "_llamar_panel", lambda *a, **k: (codigo, {}))
    assert fragmento in telegram_bot.responder("/token", CHAT)


def test_token_con_el_panel_caido_responde_claro_y_no_filtra_el_error(configurado, bot_db, monkeypatch):
    def roto(*a, **k):
        raise OSError("boom 192.168.1.44 detalle interno")

    monkeypatch.setattr(telegram_bot, "_llamar_panel", roto)
    texto = telegram_bot.responder("/token", CHAT)
    assert "No pude hablar con el panel" in texto
    assert "boom" not in texto and "192.168" not in texto


def test_llamar_panel_arma_el_pedido_y_no_valida_el_certificado_autofirmado(monkeypatch):
    capturado = {}

    def falso_urlopen(req, timeout=None, context=None):
        capturado.update(url=req.full_url, metodo=req.get_method(), cuerpo=req.data,
                         cabeceras={k.lower(): v for k, v in req.header_items()}, contexto=context)
        return _RespuestaFalsa({"ok": True})

    monkeypatch.setattr(telegram_bot.urllib.request, "urlopen", falso_urlopen)
    codigo, datos = telegram_bot._llamar_panel("/api/telegram/request", {"usuario": "Leciloft"})
    assert (codigo, datos) == (200, {"ok": True})
    assert capturado["url"] == config.PANEL_MC_URL.rstrip("/") + "/api/telegram/request"
    assert capturado["metodo"] == "POST"
    assert json.loads(capturado["cuerpo"]) == {"usuario": "Leciloft"}
    assert capturado["cabeceras"]["x-panel"] == "1"             # el panel exige esta cabecera en los POST
    assert capturado["contexto"].verify_mode == ssl.CERT_NONE
    # un GET (sin cuerpo) no manda datos
    telegram_bot._llamar_panel("/api/session")
    assert capturado["metodo"] == "GET" and capturado["cuerpo"] is None


def test_llamar_panel_devuelve_el_codigo_de_un_error_http(monkeypatch):
    def falso_urlopen(req, timeout=None, context=None):
        raise urllib.error.HTTPError(req.full_url, 429, "x", {}, io.BytesIO(b'{"error": "Demasiados intentos"}'))

    monkeypatch.setattr(telegram_bot.urllib.request, "urlopen", falso_urlopen)
    assert telegram_bot._llamar_panel("/api/telegram/request", {"usuario": "x"}) == (429, {"error": "Demasiados intentos"})


@pytest.mark.parametrize("url", ["https://8.8.8.8:8443", "https://panel.ejemplo.com:8443", ""])
def test_llamar_panel_se_niega_con_algo_que_no_sea_una_ip_privada(monkeypatch, url):
    monkeypatch.setattr(config, "PANEL_MC_URL", url)
    with pytest.raises(ValueError):
        telegram_bot._llamar_panel("/api/session")


def test_mc_muestra_el_estado_del_panel_y_sus_comandos(configurado, bot_db, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_probar_panel_mc", lambda: "disponible")
    texto, botones = telegram_bot.responder_completo("/mc", CHAT)
    assert "Panel: disponible" in texto and "/panelservidormc" in texto
    assert ("Código de acceso", "cmd:token") in botones[0]


def test_probar_panel_mc_distingue_disponible_y_caido(monkeypatch):
    monkeypatch.setattr(telegram_bot, "_llamar_panel", lambda *a, **k: (200, {}))
    assert telegram_bot._probar_panel_mc() == "disponible"

    def roto(*a, **k):
        raise OSError("sin ruta")

    monkeypatch.setattr(telegram_bot, "_llamar_panel", roto)
    assert telegram_bot._probar_panel_mc().startswith("NO responde")


# ─── /servicios y botones ─────────────────────────────────────────────────────

def _datos(botones):
    return [d for fila in botones for _, d in fila]


def test_servicios_ofrece_los_tres_servicios_con_botones(configurado, bot_db):
    texto, botones = telegram_bot.responder_completo("/servicios", CHAT)
    assert _datos(botones) == ["cmd:qpc", "cmd:mc", "cmd:pos"]


def test_cada_servicio_tiene_sus_comandos(configurado, bot_db, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_probar_panel_mc", lambda: "disponible")
    assert "cmd:cursar" in _datos(telegram_bot.responder_boton("cmd:qpc", CHAT)[1])
    assert "cmd:token" in _datos(telegram_bot.responder_boton("cmd:mc", CHAT)[1])
    assert "pos:alta" in _datos(telegram_bot.responder_boton("cmd:pos", CHAT)[1])


def test_estado_de_qpc_sigue_disponible_como_qpcestado(configurado, bot_db, monkeypatch):
    _usuario(bot_db, "dueno@mail.com")
    monkeypatch.setattr(telegram_bot, "_probar_publico", lambda: "OK (HTTP 200, 90 ms)")
    texto = telegram_bot.responder_boton("cmd:qpcestado", CHAT)[0]
    assert "Servicios de Qué Puedo Cursar" in texto


def test_botones_de_un_chat_ajeno_se_ignoran(configurado, bot_db):
    assert telegram_bot.responder_boton("cmd:servicios", "999") is None
    assert telegram_bot.responder_boton("pos:borrar!:juan", "999") is None


def test_enviar_mensaje_con_botones_arma_el_teclado(configurado, monkeypatch):
    vistos = {}

    def falso(req, timeout):
        vistos["datos"] = json.loads(req.data.decode("utf-8"))
        return _RespuestaFalsa({"ok": True})

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", falso)
    assert telegram_api.enviar_mensaje("elegí", botones=[[("A", "a"), ("B", "b")]]) is True
    assert vistos["datos"]["reply_markup"] == {"inline_keyboard": [[
        {"text": "A", "callback_data": "a"}, {"text": "B", "callback_data": "b"}]]}


def test_toque_de_boton_se_contesta_y_responde_con_botones(configurado, bot_db, monkeypatch):
    enviados, contestados = [], []
    monkeypatch.setattr(telegram_api, "enviar_mensaje", lambda texto, chat_id=None, botones=None: enviados.append((chat_id, botones)) or True)
    monkeypatch.setattr(telegram_api, "responder_boton", lambda cid: contestados.append(cid))
    telegram_bot._procesar_update({"update_id": 1, "callback_query": {
        "id": "cb1", "data": "cmd:servicios", "message": {"chat": {"id": 555}}}})
    assert contestados == ["cb1"]
    assert enviados[0][0] == 555 and _datos(enviados[0][1]) == ["cmd:qpc", "cmd:mc", "cmd:pos"]


# ─── POS Takana: el alta de usuarios es sólo por acá ──────────────────────────

@pytest.fixture
def pos(configurado, bot_db, monkeypatch):
    """POS configurado y con _llamar_pos falso: registra los pedidos."""
    monkeypatch.setattr(config, "POS_INTERNO_TOKEN", "TOKEN-INTERNO-POS")
    monkeypatch.setattr(telegram_bot, "_esperando", {})
    pedidos = []
    respuestas = {}

    def falso(metodo, ruta, cuerpo=None, timeout=8):
        pedidos.append((metodo, ruta, cuerpo))
        return respuestas.get((metodo, ruta), (200, {}))

    monkeypatch.setattr(telegram_bot, "_llamar_pos", falso)
    return pedidos, respuestas


def test_alta_pide_nombre_despues_rol_y_crea_con_contrasena_aleatoria(pos):
    pedidos, respuestas = pos
    assert "Escribí el nombre" in telegram_bot.responder_boton("pos:alta", CHAT)[0]

    texto, botones = telegram_bot.responder_completo("Juan.Perez", CHAT)
    assert "juan.perez" in texto
    assert "pos:rol:vendedor:juan.perez" in _datos(botones)
    assert pedidos == []  # todavía no se creó nada

    respuestas[("POST", "/api/interno/usuarios")] = (200, {"username": "juan.perez", "rol": "vendedor", "contrasena": "Xy9-aleatoria"})
    texto = telegram_bot.responder_boton("pos:rol:vendedor:juan.perez", CHAT)[0]
    assert pedidos == [("POST", "/api/interno/usuarios", {"username": "juan.perez", "rol": "vendedor"})]
    assert "Xy9-aleatoria" in texto and "TOKEN-INTERNO-POS" not in texto


def test_alta_rechaza_nombres_invalidos_sin_tocar_el_pos(pos):
    pedidos, _ = pos
    telegram_bot.responder_boton("pos:alta", CHAT)
    texto, _ = telegram_bot.responder_completo("juan perez", CHAT)
    assert "no sirve" in texto
    assert telegram_bot.responder_boton("pos:rol:root:juan", CHAT) is None
    assert telegram_bot.responder_boton("pos:rol:admin:a b", CHAT) is None
    assert pedidos == []


def test_alta_se_corta_con_un_comando(pos):
    telegram_bot.responder_completo("/posalta", CHAT)
    assert telegram_bot.responder_completo("/cancelar", CHAT)[0] == "Listo, cancelado."
    assert telegram_bot.responder_completo("juan", CHAT)[0].startswith("No entendí")


def test_alta_muestra_el_error_del_pos(pos):
    _, respuestas = pos
    respuestas[("POST", "/api/interno/usuarios")] = (409, {"detail": "Ese usuario ya existe"})
    assert "Ese usuario ya existe" in telegram_bot.responder_boton("pos:rol:admin:juan", CHAT)[0]


def test_sin_token_del_pos_lo_explica_y_no_llama(pos, monkeypatch):
    pedidos, _ = pos
    monkeypatch.setattr(config, "POS_INTERNO_TOKEN", "")
    assert "POS_INTERNO_TOKEN" in telegram_bot.responder_completo("/posalta", CHAT)[0]
    assert "POS_INTERNO_TOKEN" in telegram_bot.responder_completo("/posusuarios", CHAT)[0]
    assert pedidos == []


def test_borrar_y_nueva_contrasena_piden_confirmacion(pos):
    pedidos, respuestas = pos
    texto, botones = telegram_bot.responder_boton("pos:borrar:juan", CHAT)
    assert "pos:borrar!:juan" in _datos(botones) and pedidos == []
    telegram_bot.responder_boton("pos:borrar!:juan", CHAT)
    assert pedidos[-1] == ("DELETE", "/api/interno/usuarios/juan", None)

    texto, botones = telegram_bot.responder_boton("pos:clave:juan", CHAT)
    assert "pos:clave!:juan" in _datos(botones) and len(pedidos) == 1
    respuestas[("POST", "/api/interno/usuarios/juan/contrasena")] = (200, {"username": "juan", "contrasena": "nueva-123"})
    assert "nueva-123" in telegram_bot.responder_boton("pos:clave!:juan", CHAT)[0]


def test_usuarios_lista_con_botones_por_usuario(pos):
    _, respuestas = pos
    respuestas[("GET", "/api/interno/usuarios")] = (200, [{"username": "juan", "rol": "vendedor"}])
    texto, botones = telegram_bot.responder_completo("/posusuarios", CHAT)
    assert "juan (vendedor)" in texto
    assert {"pos:clave:juan", "pos:borrar:juan", "pos:alta"} <= set(_datos(botones))


def test_llamar_pos_manda_el_token_y_solo_a_una_direccion_local(monkeypatch):
    monkeypatch.setattr(config, "POS_INTERNO_TOKEN", "TOKEN-INTERNO-POS")
    capturado = {}

    def falso_urlopen(req, timeout=None):
        capturado.update(url=req.full_url, cabeceras={k.lower(): v for k, v in req.header_items()})
        return _RespuestaFalsa([])

    monkeypatch.setattr(telegram_bot.urllib.request, "urlopen", falso_urlopen)
    assert telegram_bot._llamar_pos("GET", "/api/interno/usuarios") == (200, [])
    assert capturado["url"] == "http://127.0.0.1:8060/api/interno/usuarios"
    assert capturado["cabeceras"]["x-interno-token"] == "TOKEN-INTERNO-POS"

    monkeypatch.setattr(config, "POS_API_URL", "https://posapi.takana.online")
    with pytest.raises(ValueError):
        telegram_bot._llamar_pos("GET", "/api/interno/usuarios")

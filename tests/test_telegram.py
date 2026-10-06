"""
test_telegram.py — Bot de Telegram: cliente de la API, comandos, seguridad
(sólo el chat del dueño), privacidad de la agenda y recordatorio de las 21:00.

Nada de esto toca la red: urlopen y el envío se reemplazan por falsos.
"""
import io
import json
import logging
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

"""
test_grupos.py — Grupos con código de invitación, logros en vivo y privacidad
del WebSocket.

Cubre: alta/unión/preferencias/salida, apodos, migración de esquema, que el
progreso de un usuario NO llegue a otros, y los eventos `logro_grupo` y
`grupo_miembros`.
"""
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import auth, grupos_service, models
from app.migraciones import aplicar_migraciones
from app.routers import grupos as grupos_router


class Persona:
    def __init__(self, usuario_id, token):
        self.id = usuario_id
        self.token = token
        self.h = {"Authorization": "Bearer " + token}


def _persona(db, email):
    usuario = models.Usuario(email=email, password_hash=auth.hash_password("pass12345"), rol=models.RolEnum.USER)
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    sesion = auth.crear_sesion(db, usuario)
    return Persona(usuario.id, sesion.token)


def _materia(db, nombre="Análisis Matemático II"):
    materia = models.Materia(nombre=nombre, codigo=str(uuid.uuid4())[:8])
    db.add(materia)
    db.commit()
    db.refresh(materia)
    return materia.id


@pytest.fixture(autouse=True)
def _estado_limpio():
    grupos_router.limiter.reset()
    grupos_service._anuncios_recientes.clear()
    yield
    grupos_service._anuncios_recientes.clear()


@pytest.fixture
def ws_db(db_session, monkeypatch):
    """El endpoint /ws y las notificaciones de presencia abren su propia
    sesión (SessionLocal); en los tests tienen que apuntar a la base en
    memoria, no al archivo real."""
    fabrica = sessionmaker(autocommit=False, autoflush=False, bind=db_session.bind)
    monkeypatch.setattr("app.main.SessionLocal", fabrica)
    monkeypatch.setattr("app.grupos_service.SessionLocal", fabrica)
    return db_session


def _hasta(ws, predicado, max_mensajes=30):
    """Lee mensajes del WebSocket hasta que uno cumpla el predicado; devuelve
    (mensaje, todos_los_leidos)."""
    leidos = []
    for _ in range(max_mensajes):
        mensaje = ws.receive_json()
        leidos.append(mensaje)
        if predicado(mensaje):
            return mensaje, leidos
    raise AssertionError("No llegó el mensaje esperado. Leídos: {}".format([m["event"] for m in leidos]))


def _es(evento):
    return lambda m: m["event"] == evento


def _grupo_de_dos(client, db_session):
    a = _persona(db_session, "a@grupo.com")
    b = _persona(db_session, "b@grupo.com")
    creado = client.post("/grupos", json={"nombre": "Los cobayos"}, headers=a.h)
    assert creado.status_code == 201
    unido = client.post("/grupos/unirse", json={"codigo": creado.json()["codigo"]}, headers=b.h)
    assert unido.status_code == 200
    return a, b, creado.json()["codigo"]


# ─── Apodo ────────────────────────────────────────────────────────────────────

def test_apodo_por_defecto_no_expone_el_email(client, db_session):
    p = _persona(db_session, "secreto@correo.com")
    me = client.get("/auth/me", headers=p.h).json()
    assert me["apodo"] == "Cobayo {}".format(p.id)
    assert "secreto" not in me["apodo"]


def test_cambiar_apodo_acepta_acentos_y_rechaza_invalidos(client, db_session):
    p = _persona(db_session, "apodo@correo.com")
    ok = client.put("/auth/apodo", json={"apodo": "  Ñandú Ávila  "}, headers=p.h)
    assert ok.status_code == 200
    assert ok.json()["apodo"] == "Ñandú Ávila"
    assert client.get("/auth/me", headers=p.h).json()["apodo"] == "Ñandú Ávila"
    for invalido in ("a", "x" * 21, "<script>", "hola@mundo", ""):
        assert client.put("/auth/apodo", json={"apodo": invalido}, headers=p.h).status_code == 422


# ─── Crear / unirse / preferencias / salir ────────────────────────────────────

def test_crear_grupo_y_ver_el_propio(client, db_session):
    p = _persona(db_session, "crea@grupo.com")
    r = client.post("/grupos", json={"nombre": "  Estudio  "}, headers=p.h)
    assert r.status_code == 201
    g = r.json()
    assert g["nombre"] == "Estudio"
    assert len(g["codigo"]) == 8
    assert g["yo"] == {"usuario_id": p.id, "apodo": "Cobayo {}".format(p.id), "comparte": True}
    assert [m["usuario_id"] for m in g["miembros"]] == [p.id]
    assert client.get("/grupos/mio", headers=p.h).json()["id"] == g["id"]
    # Ya está en un grupo: no puede crear otro.
    assert client.post("/grupos", json={"nombre": "Otro"}, headers=p.h).status_code == 409


def test_sin_grupo_mio_devuelve_404_con_mensaje_estable(client, db_session):
    p = _persona(db_session, "solo@grupo.com")
    r = client.get("/grupos/mio", headers=p.h)
    assert r.status_code == 404
    assert r.json()["detail"] == "No estás en ningún grupo"


def test_unirse_con_codigo_sin_importar_mayusculas(client, db_session):
    a, b, codigo = _grupo_de_dos(client, db_session)
    ver = client.get("/grupos/mio", headers=b.h).json()
    assert {m["usuario_id"] for m in ver["miembros"]} == {a.id, b.id}
    c = _persona(db_session, "c@grupo.com")
    r = client.post("/grupos/unirse", json={"codigo": codigo.lower()}, headers=c.h)
    assert r.status_code == 200
    assert len(r.json()["miembros"]) == 3


def test_unirse_con_codigo_invalido_o_estando_en_un_grupo(client, db_session):
    a, b, codigo = _grupo_de_dos(client, db_session)
    c = _persona(db_session, "c2@grupo.com")
    r = client.post("/grupos/unirse", json={"codigo": "NOEXISTE"}, headers=c.h)
    assert r.status_code == 404
    assert r.json()["detail"] == "Código inválido"
    assert client.post("/grupos/unirse", json={"codigo": codigo}, headers=b.h).status_code == 409


def test_los_intentos_de_codigo_estan_limitados(client, db_session):
    p = _persona(db_session, "fuerza@bruta.com")
    estados = [client.post("/grupos/unirse", json={"codigo": "ZZZZZZZZ"}, headers=p.h).status_code for _ in range(7)]
    assert estados[:5] == [404] * 5
    assert 429 in estados[5:]


def test_endpoints_de_grupo_exigen_login(client):
    assert client.get("/grupos/mio").status_code == 401
    assert client.post("/grupos", json={"nombre": "X1"}).status_code == 401
    assert client.post("/grupos/unirse", json={"codigo": "ABCDEFGH"}).status_code == 401


def test_preferencia_comparte_y_salir_borra_el_grupo_vacio(client, db_session):
    a, b, _ = _grupo_de_dos(client, db_session)
    r = client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=b.h)
    assert r.status_code == 200
    assert r.json()["yo"]["comparte"] is False
    en_lista = {m["usuario_id"]: m["comparte"] for m in client.get("/grupos/mio", headers=a.h).json()["miembros"]}
    assert en_lista == {a.id: True, b.id: False}

    assert client.post("/grupos/salir", headers=b.h).status_code == 204
    assert client.get("/grupos/mio", headers=b.h).status_code == 404
    assert db_session.query(models.Grupo).count() == 1
    assert client.post("/grupos/salir", headers=a.h).status_code == 204
    db_session.expire_all()
    assert db_session.query(models.Grupo).count() == 0
    assert db_session.query(models.Membresia).count() == 0


# ─── Migración de esquema ─────────────────────────────────────────────────────

def test_migracion_agrega_apodo_una_sola_vez_y_hace_backup(tmp_path):
    archivo = tmp_path / "viejo.db"
    motor = create_engine("sqlite:///{}".format(archivo))
    with motor.begin() as conn:
        conn.execute(text("CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email VARCHAR NOT NULL)"))
        conn.execute(text("INSERT INTO usuarios (id, email) VALUES (1, 'a@b.com')"))

    aplicar_migraciones(motor)
    aplicar_migraciones(motor)  # idempotente

    with motor.connect() as conn:
        columnas = [f[1] for f in conn.execute(text("PRAGMA table_info(usuarios)")).fetchall()]
        assert columnas.count("apodo") == 1
        assert conn.execute(text("SELECT email FROM usuarios")).scalar() == "a@b.com"
    assert (tmp_path / "viejo.db.bak-preGrupos").exists()


# ─── WebSocket: privacidad y eventos ──────────────────────────────────────────

def test_el_progreso_de_un_usuario_no_llega_a_otros(client, ws_db):
    a = _persona(ws_db, "priv_a@grupo.com")
    ajeno = _persona(ws_db, "priv_c@grupo.com")  # sin grupo, nada en común
    materia = _materia(ws_db)
    with client.websocket_connect("/ws?token=" + a.token) as ws_a, \
            client.websocket_connect("/ws?token=" + ajeno.token) as ws_ajeno:
        r = client.put("/estados/{}".format(materia), json={"estado": "PROMOCIONADA"}, headers=a.h)
        assert r.status_code == 200
        # A recibe su propio cambio (para sincronizar sus otros dispositivos).
        propio, _ = _hasta(ws_a, _es("estado_actualizado"))
        assert propio["data"]["usuario_id"] == a.id
        # El ajeno hace su propio reset como "centinela": todo lo que le hubiera
        # llegado antes tiene que aparecer en lo que lee hasta ese evento.
        client.post("/estados/reset", headers=ajeno.h)
        _, leidos = _hasta(ws_ajeno, _es("estados_reseteados"))
        assert [m["event"] for m in leidos] == ["estados_reseteados"]


def test_logro_llega_al_grupo_y_no_a_quien_lo_hizo(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)
    materia = _materia(ws_db, "Física II")
    client.put("/auth/apodo", json={"apodo": "Ana"}, headers=a.h)
    with client.websocket_connect("/ws?token=" + a.token) as ws_a, \
            client.websocket_connect("/ws?token=" + b.token) as ws_b:
        client.put("/estados/{}".format(materia), json={"estado": "PROMOCIONADA"}, headers=a.h)
        logro, _ = _hasta(ws_b, _es("logro_grupo"))
        assert logro["data"]["usuario_id"] == a.id
        assert logro["data"]["apodo"] == "Ana"
        assert logro["data"]["materia_id"] == materia
        assert logro["data"]["materia_nombre"] == "Física II"
        assert logro["data"]["estado"] == "PROMOCIONADA"
        # A no recibe su propio logro: cierra con un centinela y revisa lo leído.
        client.put("/auth/apodo", json={"apodo": "Centinela"}, headers=a.h)
        _, leidos = _hasta(ws_a, lambda m: m["event"] == "grupo_miembros" and any(x["apodo"] == "Centinela" for x in m["data"]["miembros"]))
        assert "logro_grupo" not in [m["event"] for m in leidos]


def test_solo_se_anuncian_logros_no_otros_estados(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)
    materia = _materia(ws_db)
    with client.websocket_connect("/ws?token=" + b.token) as ws_b:
        client.put("/estados/{}".format(materia), json={"estado": "CURSANDO"}, headers=a.h)
        client.put("/estados/{}".format(materia), json={"estado": "NO_CURSADA"}, headers=a.h)
        client.put("/estados/{}".format(materia), json={"estado": "REGULAR"}, headers=a.h)
        logro, leidos = _hasta(ws_b, _es("logro_grupo"))
        assert logro["data"]["estado"] == "REGULAR"
        assert [m["event"] for m in leidos].count("logro_grupo") == 1


def test_quien_no_comparte_ni_envia_ni_recibe_logros(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)
    client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=b.h)
    materia = _materia(ws_db)
    with client.websocket_connect("/ws?token=" + b.token) as ws_b:
        # A aprueba: B (que no comparte) no se entera.
        client.put("/estados/{}".format(materia), json={"estado": "PROMOCIONADA"}, headers=a.h)
        client.put("/auth/apodo", json={"apodo": "Centinela"}, headers=b.h)
        _, leidos = _hasta(ws_b, lambda m: m["event"] == "grupo_miembros" and any(x["apodo"] == "Centinela" for x in m["data"]["miembros"]))
        assert "logro_grupo" not in [m["event"] for m in leidos]


def test_marcar_y_desmarcar_seguido_no_repite_el_aviso(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)
    materia = _materia(ws_db)
    with client.websocket_connect("/ws?token=" + b.token) as ws_b:
        for estado in ("PROMOCIONADA", "CURSANDO", "PROMOCIONADA"):
            client.put("/estados/{}".format(materia), json={"estado": estado}, headers=a.h)
        client.put("/auth/apodo", json={"apodo": "Centinela"}, headers=a.h)
        _, leidos = _hasta(ws_b, lambda m: m["event"] == "grupo_miembros" and any(x["apodo"] == "Centinela" for x in m["data"]["miembros"]))
        assert [m["event"] for m in leidos].count("logro_grupo") == 1


def test_presencia_en_linea_del_grupo(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)

    def b_en_linea(valor):
        return lambda m: m["event"] == "grupo_miembros" and any(
            x["usuario_id"] == b.id and x["en_linea"] is valor for x in m["data"]["miembros"]
        )

    with client.websocket_connect("/ws?token=" + a.token) as ws_a:
        with client.websocket_connect("/ws?token=" + b.token):
            _hasta(ws_a, b_en_linea(True))
        _hasta(ws_a, b_en_linea(False))

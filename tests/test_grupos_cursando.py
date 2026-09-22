"""
test_grupos_cursando.py — Fase 2: "quién cursa esto ahora".

Cubre: GET /grupos/mio/cursando (snapshot) y el evento `grupo_cursando`
(entrar/salir de CURSANDO, reset, privacidad y preferencia `comparte`).
"""
import pytest

from app import models
from tests.test_grupos import Persona, _es, _estado_limpio, _grupo_de_dos, _hasta, _materia, _persona, ws_db  # noqa: F401


def _cursar(client, materia_id, persona, estado="CURSANDO"):
    r = client.put("/estados/{}".format(materia_id), json={"estado": estado}, headers=persona.h)
    assert r.status_code == 200
    return r


# ─── GET /grupos/mio/cursando ─────────────────────────────────────────────────

def test_cursando_sin_grupo_da_404(client, db_session):
    p = _persona(db_session, "sola@cursando.com")
    r = client.get("/grupos/mio/cursando", headers=p.h)
    assert r.status_code == 404
    assert r.json()["detail"] == "No estás en ningún grupo"


def test_cursando_vacio_si_nadie_esta_cursando(client, db_session):
    a, _b, _ = _grupo_de_dos(client, db_session)
    assert client.get("/grupos/mio/cursando", headers=a.h).json() == []


def test_cursando_lista_materia_y_excluye_a_quien_no_comparte(client, db_session):
    a, b, _ = _grupo_de_dos(client, db_session)
    c = _persona(db_session, "c@cursando.com")
    client.post("/grupos/unirse", json={"codigo": client.get("/grupos/mio", headers=a.h).json()["codigo"]}, headers=c.h)
    client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=c.h)

    m1 = _materia(db_session, "Física I")
    m2 = _materia(db_session, "Redes de Datos")
    _cursar(client, m1, a)
    _cursar(client, m1, b)
    _cursar(client, m2, c)  # no comparte: no debe aparecer

    r = client.get("/grupos/mio/cursando", headers=a.h)
    assert r.status_code == 200
    entradas = {(e["materia_id"], e["usuario_id"]) for e in r.json()}
    assert entradas == {(m1, a.id), (m1, b.id)}


def test_dejar_de_cursar_desaparece_del_listado(client, db_session):
    a, b, _ = _grupo_de_dos(client, db_session)
    m1 = _materia(db_session)
    _cursar(client, m1, a)
    _cursar(client, m1, a, "REGULAR")
    r = client.get("/grupos/mio/cursando", headers=b.h)
    assert r.json() == []


def test_endpoint_cursando_exige_login(client):
    assert client.get("/grupos/mio/cursando").status_code == 401


# ─── Evento grupo_cursando ─────────────────────────────────────────────────────

def test_evento_al_empezar_y_al_dejar_de_cursar(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)
    materia = _materia(ws_db, "Sistemas Operativos")
    client.put("/auth/apodo", json={"apodo": "Ana"}, headers=a.h)
    with client.websocket_connect("/ws?token=" + b.token) as ws_b:
        _cursar(client, materia, a)
        entra, _ = _hasta(ws_b, _es("grupo_cursando"))
        assert entra["data"] == {"usuario_id": a.id, "apodo": "Ana", "materia_id": materia, "cursando": True}

        _cursar(client, materia, a, "REGULAR")
        sale, _ = _hasta(ws_b, _es("grupo_cursando"))
        assert sale["data"] == {"usuario_id": a.id, "apodo": "Ana", "materia_id": materia, "cursando": False}


def test_cambiar_entre_estados_no_cursando_no_dispara_evento(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)
    materia = _materia(ws_db)
    with client.websocket_connect("/ws?token=" + b.token) as ws_b:
        _cursar(client, materia, a, "REGULAR")
        _cursar(client, materia, a, "PROMOCIONADA")
        # Centinela: si hubiera un grupo_cursando de por medio, aparecería antes.
        client.put("/auth/apodo", json={"apodo": "Centinela"}, headers=a.h)
        _, leidos = _hasta(ws_b, lambda m: m["event"] == "grupo_miembros" and any(x["apodo"] == "Centinela" for x in m["data"]["miembros"]))
        assert "grupo_cursando" not in [m["event"] for m in leidos]


def test_evento_no_llega_a_quien_lo_hizo(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)
    materia = _materia(ws_db)
    with client.websocket_connect("/ws?token=" + b.token) as ws_b:
        _cursar(client, materia, b)  # B se pone a cursar: no debe recibir el eco de su propio cambio
        client.put("/auth/apodo", json={"apodo": "Centinela"}, headers=a.h)
        _, leidos = _hasta(ws_b, lambda m: m["event"] == "grupo_miembros" and any(x["apodo"] == "Centinela" for x in m["data"]["miembros"]))
        assert "grupo_cursando" not in [m["event"] for m in leidos]


def test_quien_no_comparte_no_envia_ni_recibe_evento(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)
    client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=b.h)
    materia = _materia(ws_db)
    with client.websocket_connect("/ws?token=" + b.token) as ws_b:
        _cursar(client, materia, a)  # A cursa: B no comparte, no debe enterarse
        client.put("/auth/apodo", json={"apodo": "Centinela"}, headers=b.h)
        _, leidos = _hasta(ws_b, lambda m: m["event"] == "grupo_miembros" and any(x["apodo"] == "Centinela" for x in m["data"]["miembros"]))
        assert "grupo_cursando" not in [m["event"] for m in leidos]


def test_reset_avisa_que_dejo_de_cursar_las_materias_en_curso(client, ws_db):
    a, b, _ = _grupo_de_dos(client, ws_db)
    m1 = _materia(ws_db, "Análisis I")
    m2 = _materia(ws_db, "Álgebra")
    _cursar(client, m1, a)
    _cursar(client, m2, a)
    with client.websocket_connect("/ws?token=" + b.token) as ws_b:
        r = client.post("/estados/reset", headers=a.h)
        assert r.status_code == 200
        vistos = set()
        for _ in range(2):
            m, _ = _hasta(ws_b, _es("grupo_cursando"))
            assert m["data"]["cursando"] is False
            vistos.add(m["data"]["materia_id"])
        assert vistos == {m1, m2}
    assert client.get("/grupos/mio/cursando", headers=b.h).json() == []

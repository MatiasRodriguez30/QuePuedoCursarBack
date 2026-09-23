"""
test_grupos_ranking.py — Ranking de materias aprobadas del grupo.

Cubre: GET /grupos/mio/ranking (orden, privacidad con comparte, desempate y soy_yo).
"""
import pytest

from app import models
from tests.test_grupos import Persona, _estado_limpio, _grupo_de_dos, _materia, _persona  # noqa: F401


def _aprobar(client, materia_id, persona, estado="PROMOCIONADA"):
    r = client.put("/estados/{}".format(materia_id), json={"estado": estado}, headers=persona.h)
    assert r.status_code == 200
    return r


def test_ranking_sin_grupo_da_404(client, db_session):
    p = _persona(db_session, "sola@ranking.com")
    r = client.get("/grupos/mio/ranking", headers=p.h)
    assert r.status_code == 404
    assert r.json()["detail"] == "No estás en ningún grupo"


def test_ranking_sin_compartir_da_403(client, db_session):
    a, _b, _ = _grupo_de_dos(client, db_session)
    client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=a.h)
    r = client.get("/grupos/mio/ranking", headers=a.h)
    assert r.status_code == 403
    assert r.json()["detail"] == "Activá 'Compartir mi progreso' para ver el ranking del grupo"


def test_ranking_tres_usuarios_orden_y_privacidad(client, db_session):
    a, b, codigo = _grupo_de_dos(client, db_session)
    c = _persona(db_session, "c@ranking.com")
    unir_c = client.post("/grupos/unirse", json={"codigo": codigo}, headers=c.h)
    assert unir_c.status_code == 200

    m1 = _materia(db_session, "Materia 1")
    m2 = _materia(db_session, "Materia 2")
    m3 = _materia(db_session, "Materia 3")

    _aprobar(client, m1, a)

    _aprobar(client, m1, b)
    _aprobar(client, m2, b)

    _aprobar(client, m1, c)
    _aprobar(client, m2, c)
    _aprobar(client, m3, c)
    client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=c.h)

    r_a = client.get("/grupos/mio/ranking", headers=a.h)
    assert r_a.status_code == 200
    ranking_a = r_a.json()
    assert len(ranking_a) == 2

    assert ranking_a[0] == {
        "posicion": 1,
        "usuario_id": b.id,
        "apodo": "Cobayo {}".format(b.id),
        "materias_aprobadas": 2,
        "soy_yo": False,
    }
    assert ranking_a[1] == {
        "posicion": 2,
        "usuario_id": a.id,
        "apodo": "Cobayo {}".format(a.id),
        "materias_aprobadas": 1,
        "soy_yo": True,
    }

    r_b = client.get("/grupos/mio/ranking", headers=b.h)
    assert r_b.status_code == 200
    ranking_b = r_b.json()
    assert ranking_b[0]["soy_yo"] is True
    assert ranking_b[1]["soy_yo"] is False


def test_ranking_desempate_por_usuario_id_ascendente(client, db_session):
    a, b, _ = _grupo_de_dos(client, db_session)
    r = client.get("/grupos/mio/ranking", headers=a.h)
    assert r.status_code == 200
    res = r.json()
    assert len(res) == 2
    primero = min(a.id, b.id)
    segundo = max(a.id, b.id)
    assert res[0]["posicion"] == 1
    assert res[0]["usuario_id"] == primero
    assert res[0]["materias_aprobadas"] == 0
    assert res[1]["posicion"] == 2
    assert res[1]["usuario_id"] == segundo
    assert res[1]["materias_aprobadas"] == 0


def test_ranking_solo_cuenta_promocionada(client, db_session):
    a, _b, _ = _grupo_de_dos(client, db_session)
    m1 = _materia(db_session, "M1")
    m2 = _materia(db_session, "M2")
    m3 = _materia(db_session, "M3")

    _aprobar(client, m1, a, "PROMOCIONADA")
    _aprobar(client, m2, a, "REGULAR")
    _aprobar(client, m3, a, "CURSANDO")

    r = client.get("/grupos/mio/ranking", headers=a.h)
    assert r.status_code == 200
    entry_a = next(x for x in r.json() if x["usuario_id"] == a.id)
    assert entry_a["materias_aprobadas"] == 1


def test_ranking_exige_login(client):
    assert client.get("/grupos/mio/ranking").status_code == 401

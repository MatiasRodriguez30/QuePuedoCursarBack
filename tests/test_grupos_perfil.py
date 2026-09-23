"""
test_grupos_perfil.py — Tests para GET /grupos/mio/perfil/{usuario_id}

Cubre:
- Exige autenticación (401).
- 404 si el usuario logueado no pertenece a ningún grupo ("No estás en ningún grupo").
- 404 si usuario_id no pertenece al mismo grupo ("Ese usuario no pertenece a tu grupo").
- Perfil propio accesible siempre (incluso con comparte=False).
- Perfil de otro miembro:
  - 403 si el que pregunta no comparte ("Activá 'Compartir mi progreso' para ver el perfil de tus compañeros").
  - 403 si el consultado no comparte ("Ese compañero no comparte su progreso").
  - 200 con datos correctos si ambos comparten.
- Contrato exacto: usuario_id, apodo, total_aprobadas, por_anio (orden asc), materias (orden desc).
"""
from datetime import datetime

import pytest

from app import models
from tests.test_grupos import (  # noqa: F401
    Persona,
    _estado_limpio,
    _grupo_de_dos,
    _materia,
    _persona,
)


def _cursar(client, materia_id, persona, estado="PROMOCIONADA"):
    r = client.put("/estados/{}".format(materia_id), json={"estado": estado}, headers=persona.h)
    assert r.status_code == 200
    return r


def test_perfil_exige_login(client):
    r = client.get("/grupos/mio/perfil/1")
    assert r.status_code == 401


def test_perfil_sin_grupo_da_404(client, db_session):
    p = _persona(db_session, "solari@perfil.com")
    r = client.get("/grupos/mio/perfil/{}".format(p.id), headers=p.h)
    assert r.status_code == 404
    assert r.json()["detail"] == "No estás en ningún grupo"


def test_perfil_usuario_no_pertenece_al_grupo_da_404(client, db_session):
    a, _, _ = _grupo_de_dos(client, db_session)
    ajeno = _persona(db_session, "ajeno@perfil.com")

    r = client.get("/grupos/mio/perfil/{}".format(ajeno.id), headers=a.h)
    assert r.status_code == 404
    assert r.json()["detail"] == "Ese usuario no pertenece a tu grupo"


def test_perfil_propio_accesible_incluso_si_no_comparte(client, db_session):
    a, _, _ = _grupo_de_dos(client, db_session)
    # A desactiva compartir progreso
    r_pref = client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=a.h)
    assert r_pref.status_code == 200

    m1 = _materia(db_session, "Álgebra y Geometría")
    _cursar(client, m1, a, "PROMOCIONADA")

    r = client.get("/grupos/mio/perfil/{}".format(a.id), headers=a.h)
    assert r.status_code == 200
    data = r.json()
    assert data["usuario_id"] == a.id
    assert data["total_aprobadas"] == 1
    assert len(data["materias"]) == 1
    assert data["materias"][0]["materia_id"] == m1


def test_perfil_otro_miembro_403_si_el_que_pregunta_no_comparte(client, db_session):
    a, b, _ = _grupo_de_dos(client, db_session)
    # A desactiva compartir progreso, B mantiene comparte=True
    client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=a.h)

    r = client.get("/grupos/mio/perfil/{}".format(b.id), headers=a.h)
    assert r.status_code == 403
    assert r.json()["detail"] == "Activá 'Compartir mi progreso' para ver el perfil de tus compañeros"


def test_perfil_otro_miembro_403_si_el_consultado_no_comparte(client, db_session):
    a, b, _ = _grupo_de_dos(client, db_session)
    # B desactiva compartir progreso, A mantiene comparte=True
    client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=b.h)

    r = client.get("/grupos/mio/perfil/{}".format(b.id), headers=a.h)
    assert r.status_code == 403
    assert r.json()["detail"] == "Ese compañero no comparte su progreso"


def test_perfil_otro_miembro_403_si_ambos_no_comparten(client, db_session):
    a, b, _ = _grupo_de_dos(client, db_session)
    client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=a.h)
    client.put("/grupos/mio/preferencias", json={"comparte": False}, headers=b.h)

    # Si el que pregunta no comparte, debe primar la validación del que pregunta
    r = client.get("/grupos/mio/perfil/{}".format(b.id), headers=a.h)
    assert r.status_code == 403
    assert r.json()["detail"] == "Activá 'Compartir mi progreso' para ver el perfil de tus compañeros"


def test_perfil_otro_miembro_200_con_datos_correctos_y_orden(client, db_session):
    a, b, _ = _grupo_de_dos(client, db_session)
    # Asignamos apodo a B
    client.put("/auth/apodo", json={"apodo": "Tito"}, headers=b.h)

    m1 = _materia(db_session, "Análisis Matemático I")
    m2 = _materia(db_session, "Álgebra")
    m3 = _materia(db_session, "Física I")
    m4 = _materia(db_session, "Química")

    _cursar(client, m1, b, "PROMOCIONADA")
    _cursar(client, m2, b, "PROMOCIONADA")
    _cursar(client, m3, b, "PROMOCIONADA")
    _cursar(client, m4, b, "REGULAR")  # No es promocionada: no debe aparecer

    # Ajustar fechas de aprobación en la base de datos para verificar orden
    e1 = db_session.query(models.EstadoMateria).filter_by(usuario_id=b.id, materia_id=m1).first()
    e1.fecha_aprobacion = datetime(2023, 5, 10, 14, 30, 0)
    e2 = db_session.query(models.EstadoMateria).filter_by(usuario_id=b.id, materia_id=m2).first()
    e2.fecha_aprobacion = datetime(2024, 11, 20, 10, 0, 0)
    e3 = db_session.query(models.EstadoMateria).filter_by(usuario_id=b.id, materia_id=m3).first()
    e3.fecha_aprobacion = datetime(2024, 7, 15, 9, 0, 0)
    db_session.commit()

    # A consulta el perfil de B
    r = client.get("/grupos/mio/perfil/{}".format(b.id), headers=a.h)
    assert r.status_code == 200
    data = r.json()

    assert data["usuario_id"] == b.id
    assert data["apodo"] == "Tito"
    assert data["total_aprobadas"] == 3

    # por_anio ordenado por anio ASCENDENTE
    assert data["por_anio"] == [
        {"anio": 2023, "cantidad": 1},
        {"anio": 2024, "cantidad": 2},
    ]

    # materias ordenado por fecha_aprobacion DESCENDENTE
    # 2024-11-20 (m2), luego 2024-07-15 (m3), luego 2023-05-10 (m1)
    materias_ids = [m["materia_id"] for m in data["materias"]]
    assert materias_ids == [m2, m3, m1]

    # Validar campos de cada materia
    m_primera = data["materias"][0]
    assert m_primera["materia_id"] == m2
    assert "codigo" in m_primera
    assert m_primera["nombre"] == "Álgebra"
    assert "2024-11-20" in m_primera["fecha_aprobacion"]

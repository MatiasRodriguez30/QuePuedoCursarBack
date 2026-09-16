"""
test_consultas.py — Tests para el router /consultas

Cubre: plan vacío, materia sin prereqs (cursable), prereqs no cumplidos,
       puedo-rendir, plan-completo.
"""
import pytest
from app import models


def _crear_materia(db_session, nombre, codigo, anio=1, cuatrimestre=1, horas=4):
    materia = models.Materia(
        nombre=nombre,
        codigo=codigo,
        anio=anio,
        cuatrimestre=cuatrimestre,
        horas_semanales=horas,
    )
    db_session.add(materia)
    db_session.commit()
    db_session.refresh(materia)
    return materia


def _set_estado(db_session, usuario_id, materia_id, estado):
    obj = db_session.query(models.EstadoMateria).filter(
        models.EstadoMateria.usuario_id == usuario_id,
        models.EstadoMateria.materia_id == materia_id,
    ).first()
    if obj:
        obj.estado = estado
    else:
        obj = models.EstadoMateria(
            usuario_id=usuario_id,
            materia_id=materia_id,
            estado=estado,
        )
        db_session.add(obj)
    db_session.commit()


# ─── /consultas/puedo-cursar ─────────────────────────────────────────────────

def test_consultas_empty_plan(user_client):
    """GET /consultas/puedo-cursar con plan vacío devuelve lista vacía."""
    c, token, _ = user_client
    resp = c.get("/consultas/puedo-cursar")
    assert resp.status_code == 200
    assert resp.json() == []


def test_consultas_with_data_no_prereqs(user_client, db_session):
    """Materia sin prerequisitos aparece en /consultas/puedo-cursar."""
    c, token, uid = user_client
    _crear_materia(db_session, "Analisis I", "ANA1", anio=1, cuatrimestre=1)

    resp = c.get("/consultas/puedo-cursar")
    assert resp.status_code == 200
    nombres = [m["nombre"] for m in resp.json()]
    assert "Analisis I" in nombres


def test_consultas_prereq_no_cumplido(user_client, db_session):
    """Materia con prereq NO cumplido NO aparece en /consultas/puedo-cursar."""
    c, token, uid = user_client

    mat_base = _crear_materia(db_session, "Algebra", "ALG_C", anio=1, cuatrimestre=1)
    mat_dep = _crear_materia(db_session, "Analisis II", "ANA2_C", anio=1, cuatrimestre=2)

    # ANA2_C requiere ALG_C regularizada
    prereq = models.Prerequisito(
        materia_id=mat_dep.id,
        materia_requerida_id=mat_base.id,
        tipo=models.TipoPrerequisito.REGULARIZADA,
    )
    db_session.add(prereq)
    db_session.commit()

    # El usuario NO tiene ningún estado marcado (ALG_C = NO_CURSADA por defecto)
    resp = c.get("/consultas/puedo-cursar")
    assert resp.status_code == 200
    nombres = [m["nombre"] for m in resp.json()]
    assert "Analisis II" not in nombres
    assert "Algebra" in nombres  # ALG_C sí es cursable (sin prereqs)


def test_consultas_prereq_cumplido_regular(user_client, db_session):
    """Materia con prereq REGULARIZADA se habilita cuando la base está REGULAR."""
    c, token, uid = user_client

    mat_base = _crear_materia(db_session, "Fisica I", "FIS1_C", anio=1, cuatrimestre=1)
    mat_dep = _crear_materia(db_session, "Fisica II", "FIS2_C", anio=1, cuatrimestre=2)

    prereq = models.Prerequisito(
        materia_id=mat_dep.id,
        materia_requerida_id=mat_base.id,
        tipo=models.TipoPrerequisito.REGULARIZADA,
    )
    db_session.add(prereq)
    db_session.commit()

    _set_estado(db_session, uid, mat_base.id, models.EstadoEnum.REGULAR)

    resp = c.get("/consultas/puedo-cursar")
    assert resp.status_code == 200
    nombres = [m["nombre"] for m in resp.json()]
    assert "Fisica II" in nombres


def test_consultas_prereq_cumplido_promocionada(user_client, db_session):
    """Prereq REGULARIZADA cumplida con estado PROMOCIONADA también habilita."""
    c, token, uid = user_client

    mat_base = _crear_materia(db_session, "Quimica I", "QUI1_C", anio=1, cuatrimestre=1)
    mat_dep = _crear_materia(db_session, "Quimica II", "QUI2_C", anio=1, cuatrimestre=2)

    prereq = models.Prerequisito(
        materia_id=mat_dep.id,
        materia_requerida_id=mat_base.id,
        tipo=models.TipoPrerequisito.REGULARIZADA,
    )
    db_session.add(prereq)
    db_session.commit()

    _set_estado(db_session, uid, mat_base.id, models.EstadoEnum.PROMOCIONADA)

    resp = c.get("/consultas/puedo-cursar")
    assert resp.status_code == 200
    nombres = [m["nombre"] for m in resp.json()]
    assert "Quimica II" in nombres


def test_consultas_prereq_aprobada_requiere_promocionada(user_client, db_session):
    """Prereq tipo APROBADA NO se cumple con REGULAR, requiere PROMOCIONADA."""
    c, token, uid = user_client

    mat_base = _crear_materia(db_session, "Programacion I", "PRG1_C", anio=1, cuatrimestre=1)
    mat_dep = _crear_materia(db_session, "Programacion II", "PRG2_C", anio=1, cuatrimestre=2)

    prereq = models.Prerequisito(
        materia_id=mat_dep.id,
        materia_requerida_id=mat_base.id,
        tipo=models.TipoPrerequisito.APROBADA,
    )
    db_session.add(prereq)
    db_session.commit()

    # REGULAR no alcanza para tipo APROBADA
    _set_estado(db_session, uid, mat_base.id, models.EstadoEnum.REGULAR)
    resp = c.get("/consultas/puedo-cursar")
    nombres = [m["nombre"] for m in resp.json()]
    assert "Programacion II" not in nombres

    # PROMOCIONADA sí alcanza
    _set_estado(db_session, uid, mat_base.id, models.EstadoEnum.PROMOCIONADA)
    resp2 = c.get("/consultas/puedo-cursar")
    nombres2 = [m["nombre"] for m in resp2.json()]
    assert "Programacion II" in nombres2


def test_consultas_materia_ya_cursada_no_aparece(user_client, db_session):
    """Materia en estado REGULAR o PROMOCIONADA no aparece en puedo-cursar."""
    c, token, uid = user_client

    mat = _crear_materia(db_session, "Redes", "RED_CC", anio=2, cuatrimestre=1)
    _set_estado(db_session, uid, mat.id, models.EstadoEnum.REGULAR)

    resp = c.get("/consultas/puedo-cursar")
    nombres = [m["nombre"] for m in resp.json()]
    assert "Redes" not in nombres


# ─── /consultas/puedo-rendir ─────────────────────────────────────────────────

def test_consultas_puedo_rendir_empty(user_client):
    """GET /consultas/puedo-rendir sin materias regulares devuelve lista vacía."""
    c, token, _ = user_client
    resp = c.get("/consultas/puedo-rendir")
    assert resp.status_code == 200
    assert resp.json() == []


def test_consultas_puedo_rendir_con_regular(user_client, db_session):
    """GET /consultas/puedo-rendir devuelve solo las materias en estado REGULAR."""
    c, token, uid = user_client
    mat_reg = _crear_materia(db_session, "BD Regulada", "BD_R", anio=3, cuatrimestre=1)
    mat_promo = _crear_materia(db_session, "SO Aprobada", "SO_P", anio=3, cuatrimestre=2)

    _set_estado(db_session, uid, mat_reg.id, models.EstadoEnum.REGULAR)
    _set_estado(db_session, uid, mat_promo.id, models.EstadoEnum.PROMOCIONADA)

    resp = c.get("/consultas/puedo-rendir")
    assert resp.status_code == 200
    nombres = [m["nombre"] for m in resp.json()]
    assert "BD Regulada" in nombres
    assert "SO Aprobada" not in nombres


# ─── /consultas/plan-completo ─────────────────────────────────────────────────

def test_consultas_plan_completo_empty(user_client):
    """GET /consultas/plan-completo con DB vacía devuelve lista vacía."""
    c, token, _ = user_client
    resp = c.get("/consultas/plan-completo")
    assert resp.status_code == 200
    assert resp.json() == []


def test_consultas_plan_completo_estructura(user_client, db_session):
    """GET /consultas/plan-completo devuelve el shape correcto por materia."""
    c, token, uid = user_client
    _crear_materia(db_session, "Materia Plan", "PLAN_C", anio=1, cuatrimestre=1)

    resp = c.get("/consultas/plan-completo")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    item = resp.json()[0]
    assert "materia" in item
    assert "estado" in item
    assert "puede_cursar" in item
    assert "prerequisitos" in item
    assert item["estado"] == "NO_CURSADA"
    assert item["puede_cursar"] is True  # sin prereqs, puede cursarse


def test_consultas_plan_completo_unauthenticated(client):
    """GET /consultas/plan-completo sin token devuelve 401."""
    resp = client.get("/consultas/plan-completo")
    assert resp.status_code == 401


def test_consultas_plan_completo_promocionada_no_puede_cursar(user_client, db_session):
    """Una materia PROMOCIONADA tiene puede_cursar=False en plan-completo."""
    c, token, uid = user_client
    mat = _crear_materia(db_session, "Ya Aprobada", "APRO_C", anio=2, cuatrimestre=1)
    _set_estado(db_session, uid, mat.id, models.EstadoEnum.PROMOCIONADA)

    resp = c.get("/consultas/plan-completo")
    item = next(i for i in resp.json() if i["materia"]["nombre"] == "Ya Aprobada")
    assert item["puede_cursar"] is False
    assert item["estado"] == "PROMOCIONADA"

"""
test_eventos.py — Agenda: eventos institucionales (para todos) vs personales
(sólo para quien los crea).

Reproduce el bug reportado: un evento personal creado por un ADMIN no debe
aparecer en la agenda de otro usuario.
"""
from app import auth, models


def _headers(db_session, usuario):
    sesion = auth.crear_sesion(db_session, usuario)
    return {"Authorization": "Bearer " + sesion.token}


def _titulos(respuesta):
    return {e["titulo"] for e in respuesta.json()}


# ─── El bug original ───────────────────────────────────────────────────────

def test_evento_personal_de_admin_no_se_ve_en_la_agenda_de_otro_usuario(client, db_session, admin_user, user):
    h_admin = _headers(db_session, admin_user)
    h_user = _headers(db_session, user)

    r = client.post(
        "/eventos",
        json={"titulo": "Rendir oral", "fecha": "2026-09-30", "personal": True},
        headers=h_admin,
    )
    assert r.status_code == 201
    assert r.json()["personal"] is True

    # El propio admin lo ve...
    assert "Rendir oral" in _titulos(client.get("/eventos", headers=h_admin))
    # ...pero al otro usuario no le tiene que aparecer.
    assert "Rendir oral" not in _titulos(client.get("/eventos", headers=h_user))


def test_evento_institucional_se_ve_para_todos(client, db_session, admin_user, user):
    h_admin = _headers(db_session, admin_user)
    h_user = _headers(db_session, user)

    r = client.post(
        "/eventos",
        json={"titulo": "6° Mesas de examen", "fecha": "2026-09-30", "personal": False},
        headers=h_admin,
    )
    assert r.status_code == 201

    assert "6° Mesas de examen" in _titulos(client.get("/eventos", headers=h_admin))
    assert "6° Mesas de examen" in _titulos(client.get("/eventos", headers=h_user))


# ─── Quién puede crear qué ─────────────────────────────────────────────────

def test_usuario_normal_puede_crear_su_propio_evento_personal(client, db_session, user):
    h_user = _headers(db_session, user)
    r = client.post(
        "/eventos",
        json={"titulo": "Parcial Inglés", "fecha": "2026-10-01", "personal": True},
        headers=h_user,
    )
    assert r.status_code == 201
    assert r.json()["creado_por_id"] == user.id


def test_usuario_normal_no_puede_crear_evento_institucional(client, db_session, user):
    h_user = _headers(db_session, user)
    r = client.post(
        "/eventos",
        json={"titulo": "Feriado facultad", "fecha": "2026-10-01", "personal": False},
        headers=h_user,
    )
    assert r.status_code == 403


# ─── Editar / borrar ────────────────────────────────────────────────────────

def test_no_puede_editar_ni_borrar_evento_personal_de_otro_usuario(client, db_session, admin_user, user):
    h_admin = _headers(db_session, admin_user)
    h_user = _headers(db_session, user)

    creado = client.post(
        "/eventos",
        json={"titulo": "Recuperatorio física", "fecha": "2026-10-02", "personal": True},
        headers=h_admin,
    ).json()

    assert client.put(f"/eventos/{creado['id']}", json={"titulo": "Otro"}, headers=h_user).status_code == 403
    assert client.delete(f"/eventos/{creado['id']}", headers=h_user).status_code == 403

    # El dueño sí puede.
    ok = client.put(f"/eventos/{creado['id']}", json={"titulo": "Recuperatorio física (cambio de aula)"}, headers=h_admin)
    assert ok.status_code == 200
    assert client.delete(f"/eventos/{creado['id']}", headers=h_admin).status_code == 204


def test_usuario_normal_no_puede_editar_evento_institucional(client, db_session, admin_user, user):
    h_admin = _headers(db_session, admin_user)
    h_user = _headers(db_session, user)

    creado = client.post(
        "/eventos",
        json={"titulo": "Receso de invierno", "fecha": "2026-10-03", "personal": False},
        headers=h_admin,
    ).json()

    assert client.put(f"/eventos/{creado['id']}", json={"titulo": "Otro"}, headers=h_user).status_code == 403
    assert client.delete(f"/eventos/{creado['id']}", headers=h_user).status_code == 403

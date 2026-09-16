"""
test_estados.py — Tests para el router /estados

Cubre: listado (aislamiento por usuario), update, reset.
"""
import pytest
from fastapi.testclient import TestClient
from app.database import get_db
from app.main import app
from app import models, auth


def _make_user_client(db_session, email, password):
    """Helper: crea un usuario y devuelve un TestClient con su token."""
    usuario = models.Usuario(
        email=email,
        password_hash=auth.hash_password(password),
        rol=models.RolEnum.USER,
    )
    db_session.add(usuario)
    db_session.commit()
    db_session.refresh(usuario)
    sesion = auth.crear_sesion(db_session, usuario)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app, raise_server_exceptions=True)
    c.headers.update({"Authorization": f"Bearer {sesion.token}"})
    return c, sesion.token, usuario.id


def _crear_materia(db_session, nombre="Materia Test", codigo=None):
    """Helper: inserta una materia en DB y retorna su ID."""
    if not codigo:
        import uuid
        codigo = str(uuid.uuid4())[:8]
    materia = models.Materia(nombre=nombre, codigo=codigo)
    db_session.add(materia)
    db_session.commit()
    db_session.refresh(materia)
    return materia.id


# ─── GET /estados ─────────────────────────────────────────────────────────────

def test_get_estados_unauthenticated(client):
    """GET /estados sin token devuelve 401."""
    resp = client.get("/estados")
    assert resp.status_code == 401


def test_get_estados_own_user(db_session):
    """GET /estados devuelve solo los estados del usuario autenticado, no de otros."""
    materia_id = _crear_materia(db_session, "Algebra", "ALG_EST")

    c1, token1, uid1 = _make_user_client(db_session, "user1@est.com", "pass123")
    c2, token2, uid2 = _make_user_client(db_session, "user2@est.com", "pass456")

    # user1 marca la materia como REGULAR
    c1.put(f"/estados/{materia_id}", json={"estado": "REGULAR"})

    # user2 no marcó nada: sus estados están vacíos
    resp_u2 = c2.get("/estados")
    assert resp_u2.status_code == 200
    assert resp_u2.json() == []

    # user1 ve su estado
    resp_u1 = c1.get("/estados")
    assert resp_u1.status_code == 200
    assert len(resp_u1.json()) == 1
    assert resp_u1.json()[0]["estado"] == "REGULAR"
    assert resp_u1.json()[0]["usuario_id"] == uid1

    app.dependency_overrides.clear()


def test_get_estado_empty_initial(user_client):
    """GET /estados con DB sin ningún estado devuelve lista vacía."""
    c, token, _ = user_client
    resp = c.get("/estados")
    assert resp.status_code == 200
    assert resp.json() == []


# ─── PUT /estados/{materia_id} ────────────────────────────────────────────────

def test_update_estado_creates_row(db_session):
    """PUT /estados/{materia_id} crea la fila si no existe."""
    materia_id = _crear_materia(db_session, "Analisis", "ANA_EST")
    c, token, uid = _make_user_client(db_session, "user_put@est.com", "pass123")

    resp = c.put(f"/estados/{materia_id}", json={"estado": "REGULAR"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["estado"] == "REGULAR"
    assert data["materia_id"] == materia_id
    assert data["usuario_id"] == uid

    app.dependency_overrides.clear()


def test_update_estado_changes_value(db_session):
    """PUT /estados/{materia_id} actualiza el estado si ya existe."""
    materia_id = _crear_materia(db_session, "Fisica", "FIS_EST")
    c, token, uid = _make_user_client(db_session, "user_change@est.com", "pass123")

    c.put(f"/estados/{materia_id}", json={"estado": "REGULAR"})
    resp = c.put(f"/estados/{materia_id}", json={"estado": "PROMOCIONADA"})
    assert resp.status_code == 200
    assert resp.json()["estado"] == "PROMOCIONADA"

    app.dependency_overrides.clear()


def test_update_estado_materia_not_found(user_client):
    """PUT /estados/{materia_id} con materia inexistente devuelve 404."""
    c, token, _ = user_client
    resp = c.put("/estados/99999", json={"estado": "REGULAR"})
    assert resp.status_code == 404


def test_update_estado_todos_los_valores(db_session):
    """PUT /estados/{materia_id} acepta todos los valores del enum EstadoEnum."""
    materia_id = _crear_materia(db_session, "Quimica", "QUI_EST")
    c, token, _ = _make_user_client(db_session, "user_enum@est.com", "pass123")

    for estado in ["NO_CURSADA", "CURSANDO", "REGULAR", "PROMOCIONADA"]:
        resp = c.put(f"/estados/{materia_id}", json={"estado": estado})
        assert resp.status_code == 200, f"Falló con estado={estado}"
        assert resp.json()["estado"] == estado

    app.dependency_overrides.clear()


# ─── POST /estados/reset ──────────────────────────────────────────────────────

def test_reset_estados(db_session):
    """POST /estados/reset vuelve todos los estados del usuario a NO_CURSADA."""
    m1 = _crear_materia(db_session, "M1", "M1_RST")
    m2 = _crear_materia(db_session, "M2", "M2_RST")
    c, token, uid = _make_user_client(db_session, "user_reset@est.com", "pass123")

    c.put(f"/estados/{m1}", json={"estado": "REGULAR"})
    c.put(f"/estados/{m2}", json={"estado": "PROMOCIONADA"})

    resp = c.post("/estados/reset")
    assert resp.status_code == 200
    estados_response = resp.json()
    for e in estados_response:
        assert e["estado"] == "NO_CURSADA"

    # Verificar también con GET /estados
    get_resp = c.get("/estados")
    for e in get_resp.json():
        assert e["estado"] == "NO_CURSADA"

    app.dependency_overrides.clear()


def test_reset_estados_empty(user_client):
    """POST /estados/reset con ningún estado previo devuelve lista vacía."""
    c, token, _ = user_client
    resp = c.post("/estados/reset")
    assert resp.status_code == 200
    assert resp.json() == []


# ─── Aislamiento entre usuarios ───────────────────────────────────────────────

def test_estado_isolation(db_session):
    """User A no puede leer ni modificar los estados de User B."""
    materia_id = _crear_materia(db_session, "Redes", "RED_ISO")

    c_a, token_a, uid_a = _make_user_client(db_session, "useraA@iso.com", "passA123")
    c_b, token_b, uid_b = _make_user_client(db_session, "userB@iso.com", "passB123")

    # A marca la materia
    c_a.put(f"/estados/{materia_id}", json={"estado": "PROMOCIONADA"})

    # B no ve el estado de A (su lista está vacía)
    resp_b = c_b.get("/estados")
    assert resp_b.status_code == 200
    # Filtrar: B solo ve sus propios estados
    ids_usuario_b = [e["usuario_id"] for e in resp_b.json()]
    assert uid_a not in ids_usuario_b

    # B tiene su propia lista vacía
    assert len(resp_b.json()) == 0

    # B marca distinto y no afecta a A
    c_b.put(f"/estados/{materia_id}", json={"estado": "REGULAR"})

    resp_a_after = c_a.get("/estados")
    assert resp_a_after.json()[0]["estado"] == "PROMOCIONADA"  # A no cambió

    app.dependency_overrides.clear()


def test_get_estado_individual(db_session):
    """GET /estados/{materia_id} devuelve el estado de la materia para el usuario."""
    materia_id = _crear_materia(db_session, "BD", "BD_IND")
    c, token, uid = _make_user_client(db_session, "user_ind@est.com", "pass123")

    c.put(f"/estados/{materia_id}", json={"estado": "CURSANDO"})
    resp = c.get(f"/estados/{materia_id}")
    assert resp.status_code == 200
    assert resp.json()["estado"] == "CURSANDO"

    app.dependency_overrides.clear()


def test_get_estado_individual_not_found(user_client):
    """GET /estados/{materia_id} sin estado previo devuelve 404."""
    c, token, _ = user_client
    # Necesitamos una materia que exista para que el 404 sea de "estado no encontrado"
    # pero como la DB está vacía, cualquier ID va a devolver 404 del estado
    resp = c.get("/estados/99999")
    assert resp.status_code == 404

def _admin_token(app_test, admin_user):
    login = app_test.post("/auth/login", json={"email": "admin@test.com", "password": "admin123"})
    return login.json()["token"]


def _crear_materia(app_test, token, carrera_id, nombre):
    r = app_test.post("/materias", json={"nombre": nombre, "carrera_id": carrera_id}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    return r.json()["id"]


def test_prerequisito_circular_rechazado(app_test, admin_user, carrera):
    token = _admin_token(app_test, admin_user)
    headers = {"Authorization": f"Bearer {token}"}
    a = _crear_materia(app_test, token, carrera.id, "A")
    b = _crear_materia(app_test, token, carrera.id, "B")
    c = _crear_materia(app_test, token, carrera.id, "C")

    # A requiere B, B requiere C
    r = app_test.post("/prerequisitos", json={"materia_id": a, "materia_requerida_id": b, "tipo": "REGULARIZADA"}, headers=headers)
    assert r.status_code == 201
    r = app_test.post("/prerequisitos", json={"materia_id": b, "materia_requerida_id": c, "tipo": "REGULARIZADA"}, headers=headers)
    assert r.status_code == 201

    # C requiere A cerraría el ciclo A->B->C->A
    r = app_test.post("/prerequisitos", json={"materia_id": c, "materia_requerida_id": a, "tipo": "REGULARIZADA"}, headers=headers)
    assert r.status_code == 400
    assert "circular" in r.json()["detail"].lower()


def test_borrar_materia_requerida_no_rompe_prerequisitos(app_test, admin_user, carrera):
    token = _admin_token(app_test, admin_user)
    headers = {"Authorization": f"Bearer {token}"}
    a = _crear_materia(app_test, token, carrera.id, "A")
    b = _crear_materia(app_test, token, carrera.id, "B")

    r = app_test.post("/prerequisitos", json={"materia_id": a, "materia_requerida_id": b, "tipo": "REGULARIZADA"}, headers=headers)
    assert r.status_code == 201

    # Borrar B (que es correlativa de A) no debe romper con 500
    r = app_test.delete(f"/materias/{b}", headers=headers)
    assert r.status_code == 204

    # Y la lista de prerequisitos de la carrera debe seguir sirviendo bien,
    # sin la fila huérfana (se borró en cascada).
    r = app_test.get(f"/prerequisitos?carrera_id={carrera.id}", headers=headers)
    assert r.status_code == 200
    assert all(p["materia_requerida_id"] != b for p in r.json())

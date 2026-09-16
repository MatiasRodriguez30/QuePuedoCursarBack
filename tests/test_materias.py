def test_get_materias_unauthenticated(app_test):
    response = app_test.get("/materias")
    assert response.status_code == 401

def test_create_materia_as_user(app_test, user):
    login = app_test.post("/auth/login", json={"email": "user@test.com", "password": "user123"})
    token = login.json()["token"]
    response = app_test.post("/materias", json={"nombre": "Math", "codigo": "123", "anio": 1}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403

def test_create_materia_as_admin(app_test, admin_user):
    login = app_test.post("/auth/login", json={"email": "admin@test.com", "password": "admin123"})
    token = login.json()["token"]
    response = app_test.post("/materias", json={"nombre": "Math", "codigo": "123", "anio": 1}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    assert response.json()["nombre"] == "Math"

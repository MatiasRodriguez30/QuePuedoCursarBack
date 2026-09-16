def test_register_success(app_test):
    response = app_test.post("/auth/register", json={"email": "new@test.com", "password": "password123"})
    assert response.status_code == 201
    assert "token" in response.json()

def test_login_success(app_test, user):
    response = app_test.post("/auth/login", json={"email": "user@test.com", "password": "user123"})
    assert response.status_code == 200
    assert "token" in response.json()

def test_login_wrong_password(app_test, user):
    response = app_test.post("/auth/login", json={"email": "user@test.com", "password": "wrongpassword"})
    assert response.status_code == 401

def test_get_me_unauthenticated(app_test):
    response = app_test.get("/auth/me")
    assert response.status_code == 401

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app import models, auth

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def app_test():
    # Reestablece el override por si un test anterior llamó
    # app.dependency_overrides.clear() (efecto global sobre `app`, ver
    # comentario en user_client más abajo).
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client

# Alias de app_test: algunos archivos de test usan el nombre "client" para el
# mismo TestClient anónimo (sin usuario autenticado).
@pytest.fixture
def client(app_test):
    return app_test

@pytest.fixture
def admin_user():
    db = TestingSessionLocal()
    user = models.Usuario(email="admin@test.com", password_hash=auth.hash_password("admin123"), rol=models.RolEnum.ADMIN)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture
def user():
    db = TestingSessionLocal()
    user = models.Usuario(email="user@test.com", password_hash=auth.hash_password("user123"), rol=models.RolEnum.USER)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture
def carrera():
    db = TestingSessionLocal()
    c = models.Carrera(nombre="Carrera de Prueba", plan_nombre="Plan Test", horas_excepcion_ultimo_anio=32)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c

@pytest.fixture
def db_session():
    """Sesión SQLAlchemy directa sobre la MISMA base en memoria que usa la app
    (comparten conexión gracias a StaticPool), para que los tests puedan
    insertar/leer datos sin pasar por la API."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture
def user_client(db_session):
    """TestClient ya autenticado como un usuario normal recién creado.
    Devuelve (client, token, usuario_id) — ver _make_user_client en los
    archivos de test para el mismo patrón usado ad-hoc con otros emails.

    Reestablece el override de get_db explícitamente: algunos tests llaman
    app.dependency_overrides.clear() al final (efecto GLOBAL sobre el dict
    compartido de `app`, no local a ese test), lo que rompía cualquier test
    siguiente que dependiera de este fixture si no se reponía acá.
    """
    app.dependency_overrides[get_db] = override_get_db

    usuario = models.Usuario(
        email="fixture_user@test.com",
        password_hash=auth.hash_password("pass12345"),
        rol=models.RolEnum.USER,
    )
    db_session.add(usuario)
    db_session.commit()
    db_session.refresh(usuario)
    sesion = auth.crear_sesion(db_session, usuario)

    client = TestClient(app, raise_server_exceptions=True)
    client.headers.update({"Authorization": f"Bearer {sesion.token}"})
    return client, sesion.token, usuario.id

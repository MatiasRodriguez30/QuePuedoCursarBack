"""
test_db_admin.py — Panel de base de datos para el administrador (sólo lectura).

Cubre: acceso restringido a ADMIN, introspección de tablas/columnas/FKs,
paginación de filas y que las columnas sensibles nunca viajen en claro.
"""
import uuid

import pytest

from app import auth, models


def _token(db_session, usuario):
    """Crea la sesión directo en la base, sin pasar por POST /auth/login:
    ese endpoint tiene un límite de 10 intentos por minuto (pensado para
    frenar fuerza bruta), y esta suite necesita muchos más tokens que eso."""
    return auth.crear_sesion(db_session, usuario).token


def _materia(db_session, carrera, nombre="Álgebra"):
    m = models.Materia(nombre=nombre, codigo=str(uuid.uuid4())[:8], carrera_id=carrera.id)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


# ─── Acceso ─────────────────────────────────────────────────────────────────

def test_requiere_login(app_test):
    assert app_test.get("/admin/db/tablas").status_code == 401
    assert app_test.get("/admin/db/tablas/usuarios/filas").status_code == 401


def test_usuario_comun_no_puede_entrar(app_test, user, db_session):
    headers = {"Authorization": "Bearer {}".format(_token(db_session, user))}
    assert app_test.get("/admin/db/tablas", headers=headers).status_code == 403
    assert app_test.get("/admin/db/tablas/usuarios/filas", headers=headers).status_code == 403


# ─── /admin/db/tablas ───────────────────────────────────────────────────────

def test_lista_tablas_con_columnas_y_relaciones(app_test, admin_user, db_session):
    headers = {"Authorization": "Bearer {}".format(_token(db_session, admin_user))}
    r = app_test.get("/admin/db/tablas", headers=headers)
    assert r.status_code == 200
    tablas = {t["nombre"]: t for t in r.json()}

    # Aparecen todas las tablas reales del esquema, no una lista hardcodeada.
    assert set(models.Base.metadata.tables.keys()) <= set(tablas.keys())

    materias = tablas["materias"]
    col_carrera_id = next(c for c in materias["columnas"] if c["nombre"] == "carrera_id")
    assert col_carrera_id["referencia"] == {"tabla": "carreras", "columna": "id"}

    col_id = next(c for c in materias["columnas"] if c["nombre"] == "id")
    assert col_id["primary_key"] is True
    assert col_id["referencia"] is None


def test_columnas_sensibles_marcadas_en_el_esquema(app_test, admin_user, db_session):
    headers = {"Authorization": "Bearer {}".format(_token(db_session, admin_user))}
    tablas = {t["nombre"]: t for t in app_test.get("/admin/db/tablas", headers=headers).json()}

    def col(tabla, nombre):
        return next(c for c in tablas[tabla]["columnas"] if c["nombre"] == nombre)

    assert col("usuarios", "password_hash")["sensible"] is True
    assert col("usuarios", "email")["sensible"] is False
    assert col("sesiones", "token")["sensible"] is True
    assert col("password_reset_tokens", "token")["sensible"] is True


def test_conteo_de_filas_es_real(app_test, admin_user, carrera, db_session):
    headers = {"Authorization": "Bearer {}".format(_token(db_session, admin_user))}
    _materia(db_session, carrera, "Física I")
    _materia(db_session, carrera, "Física II")

    tablas = {t["nombre"]: t for t in app_test.get("/admin/db/tablas", headers=headers).json()}
    assert tablas["materias"]["filas"] == 2


# ─── /admin/db/tablas/{nombre}/filas ───────────────────────────────────────

def test_filas_de_usuarios_ocultan_password_hash(app_test, admin_user, user, db_session):
    headers = {"Authorization": "Bearer {}".format(_token(db_session, admin_user))}
    r = app_test.get("/admin/db/tablas/usuarios/filas", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    for fila in data["filas"]:
        assert fila["password_hash"] == "••••••"
        assert fila["password_hash"] != user.password_hash
        assert "email" in fila and fila["email"]


def test_filas_de_sesiones_ocultan_token(app_test, admin_user, db_session):
    token = _token(db_session, admin_user)
    headers = {"Authorization": "Bearer {}".format(token)}
    r = app_test.get("/admin/db/tablas/sesiones/filas", headers=headers)
    assert r.status_code == 200
    filas = r.json()["filas"]
    assert len(filas) >= 1
    for fila in filas:
        assert fila["token"] == "••••••"
        assert fila["token"] != token


def test_tabla_inexistente_da_404(app_test, admin_user, db_session):
    headers = {"Authorization": "Bearer {}".format(_token(db_session, admin_user))}
    r = app_test.get("/admin/db/tablas/no_existe/filas", headers=headers)
    assert r.status_code == 404


def test_paginacion_de_filas(app_test, admin_user, carrera, db_session):
    headers = {"Authorization": "Bearer {}".format(_token(db_session, admin_user))}
    ids = [_materia(db_session, carrera, "Materia {}".format(i)).id for i in range(5)]

    r1 = app_test.get("/admin/db/tablas/materias/filas?offset=0&limit=2", headers=headers)
    r2 = app_test.get("/admin/db/tablas/materias/filas?offset=2&limit=2", headers=headers)
    assert r1.json()["total"] == 5
    assert [f["id"] for f in r1.json()["filas"]] == ids[0:2]
    assert [f["id"] for f in r2.json()["filas"]] == ids[2:4]

    grande = app_test.get("/admin/db/tablas/materias/filas?limit=500", headers=headers)
    assert grande.status_code == 422  # supera LIMITE_MAXIMO


def test_fechas_se_serializan_como_texto(app_test, admin_user, db_session):
    headers = {"Authorization": "Bearer {}".format(_token(db_session, admin_user))}
    r = app_test.get("/admin/db/tablas/usuarios/filas", headers=headers)
    fila = r.json()["filas"][0]
    assert isinstance(fila["creado_en"], str)

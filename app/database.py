from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Ruta absoluta, independiente del directorio de trabajo desde el que se
# lance uvicorn (antes usaba una ruta relativa y terminaba creando un
# plan_estudios.db distinto según el cwd).
DB_PATH = Path(__file__).resolve().parent.parent / "plan_estudios.db"
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """SQLite trae las foreign keys desactivadas por default (no se
    aplicarían los ON DELETE CASCADE de los modelos si algún código las
    saltea vía SQL crudo). WAL + busy_timeout evitan "database is locked"
    cuando el scheduler y pedidos de usuarios pisan la DB al mismo tiempo."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

from pathlib import Path

from sqlalchemy import create_engine
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
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

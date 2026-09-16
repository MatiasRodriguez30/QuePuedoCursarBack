"""
Migración v3: introduce el concepto de "Carrera" (la app puede trackear más
de una carrera universitaria, cada una con su propio plan de materias y
correlatividades, en vez de un único plan global hardcodeado para Ingeniería
en Sistemas).

Pasos:
  1. Crea la tabla `carreras` (nueva).
  2. Crea una carrera "Ingeniería en Sistemas" (Plan 2023, excepción de
     Adelanto de Nivel de 32hs) para no perder el significado de los datos
     ya cargados.
  3. Agrega la columna `carrera_id` a `materias` y a `config_app`, y la
     completa con el id de esa carrera para TODAS las filas existentes.
  4. Reemplaza el índice único global de `materias.codigo` (`ix_materias_codigo`)
     por uno compuesto (carrera_id, codigo): dos carreras distintas ya
     pueden reusar el mismo código.
  5. Le pone un índice único a `config_app.carrera_id` (antes era una fila
     fija id=1; ahora es una fila por carrera).

Es seguro correr este script más de una vez: si `materias` ya tiene la
columna `carrera_id`, no hace nada.

IMPORTANTE: hacé un backup de plan_estudios.db antes de correrlo en
producción:
    cp plan_estudios.db plan_estudios.db.bak-preCarreras

Uso:
    python scripts/migrate_v3_carreras.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import DB_PATH, engine
from app import models

NOMBRE_CARRERA_DEFAULT = "Ingeniería en Sistemas"
PLAN_DEFAULT = "Plan 2023"
HORAS_EXCEPCION_DEFAULT = 32


def columna_existe(conn, tabla, columna) -> bool:
    filas = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
    return any(f[1] == columna for f in filas)


def main():
    conn = sqlite3.connect(str(DB_PATH))

    if columna_existe(conn, "materias", "carrera_id"):
        print("Ya migrado (materias.carrera_id existe). No se hace nada.")
        conn.close()
        return

    # 1. Crea la tabla `carreras` (create_all sólo crea tablas que faltan,
    # no toca las que ya existen).
    models.Base.metadata.create_all(bind=engine)

    cur = conn.cursor()

    # 2. Carrera por defecto.
    cur.execute(
        "INSERT INTO carreras (nombre, plan_nombre, horas_excepcion_ultimo_anio) VALUES (?, ?, ?)",
        (NOMBRE_CARRERA_DEFAULT, PLAN_DEFAULT, HORAS_EXCEPCION_DEFAULT),
    )
    carrera_id = cur.lastrowid
    print(f"Carrera creada: '{NOMBRE_CARRERA_DEFAULT}' (id={carrera_id})")

    # 3. Agrega carrera_id a materias y la completa.
    cur.execute("ALTER TABLE materias ADD COLUMN carrera_id INTEGER REFERENCES carreras(id)")
    cur.execute("UPDATE materias SET carrera_id = ?", (carrera_id,))
    print(f"{cur.rowcount} materias asignadas a '{NOMBRE_CARRERA_DEFAULT}'")

    # 4. Índice único global -> compuesto por carrera.
    cur.execute("DROP INDEX IF EXISTS ix_materias_codigo")
    cur.execute("CREATE UNIQUE INDEX uq_materia_carrera_codigo ON materias (carrera_id, codigo)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_materias_codigo ON materias (codigo)")

    # 5. config_app: de fila única global a una fila por carrera.
    cur.execute("ALTER TABLE config_app ADD COLUMN carrera_id INTEGER REFERENCES carreras(id)")
    cur.execute("UPDATE config_app SET carrera_id = ?", (carrera_id,))
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_config_app_carrera ON config_app (carrera_id)")

    conn.commit()
    conn.close()
    print("Migración completa.")


if __name__ == "__main__":
    main()

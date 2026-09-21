"""Migraciones livianas que corren solas al arrancar el servidor.

`Base.metadata.create_all` crea tablas nuevas pero NO agrega columnas a
tablas que ya existen. Como el auto-deploy de la tablet no corre scripts,
las columnas nuevas se agregan acá, de forma idempotente (se pueden ejecutar
las veces que sea: si ya está, no hace nada).
"""
import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy import text


def _columnas(conn, tabla: str):
    return {fila[1] for fila in conn.execute(text("PRAGMA table_info({})".format(tabla))).fetchall()}


def _backup(engine, sufijo: str) -> Optional[Path]:
    """Copia la base antes de tocar el esquema. Devuelve la ruta, o None si
    no es una base en archivo o el backup ya existía."""
    ruta = engine.url.database
    if not ruta or ruta == ":memory:":
        return None
    origen = Path(ruta)
    if not origen.exists():
        return None
    destino = origen.with_name(origen.name + ".bak-" + sufijo)
    if destino.exists():
        return None
    with engine.connect() as conn:
        # Con WAL, lo último escrito puede estar en el archivo -wal: se vuelca
        # al archivo principal antes de copiarlo.
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(str(origen), str(destino))
    return destino


def aplicar_migraciones(engine) -> None:
    with engine.connect() as conn:
        tablas = {fila[0] for fila in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "usuarios" not in tablas:
            return
        if "apodo" in _columnas(conn, "usuarios"):
            return

    _backup(engine, "preGrupos")
    with engine.begin() as conn:
        # Re-chequeo dentro de la transacción por si otro proceso ya migró.
        if "apodo" not in _columnas(conn, "usuarios"):
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN apodo VARCHAR"))

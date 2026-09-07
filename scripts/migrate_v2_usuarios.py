"""
Migración a "v2": progreso por usuario.

Antes de correr esto:
  1. Actualizar el código (ya incluye los modelos Usuario/Sesion).
  2. Arrancar el servidor una vez (`uvicorn app.main:app`) para que
     `create_all` cree las tablas nuevas `usuarios` y `sesiones`. Frenarlo
     antes de seguir.

Qué hace este script:
  - Recrea `estados_materia` sin el UNIQUE(materia_id) viejo (SQLite no
    permite dropear ese constraint con ALTER TABLE), agregando la columna
    `usuario_id` y el nuevo UNIQUE(usuario_id, materia_id).
  - Las filas existentes (progreso cargado antes de que existieran los
    usuarios) quedan con usuario_id NULL — "huérfanas" — hasta que alguien
    las reclame (ver scripts/reclamar_progreso.py).

Es idempotente: si ya se corrió antes (la columna usuario_id ya existe), no
hace nada.

Uso:
    python scripts/migrate_v2_usuarios.py [ruta_a_la_db]
    (default: plan_estudios.db en la raíz del proyecto)
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent.parent / "plan_estudios.db")


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(estados_materia)")
    columnas = [row[1] for row in cur.fetchall()]
    if "usuario_id" in columnas:
        print("Ya migrado (estados_materia ya tiene usuario_id). Nada que hacer.")
        return

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='usuarios'")
    if not cur.fetchone():
        print("ERROR: no existe la tabla 'usuarios' todavía.")
        print("Arrancá el servidor una vez con el código nuevo (para que cree las tablas) y volvé a correr esto.")
        sys.exit(1)

    print("Migrando estados_materia...")
    cur.execute("ALTER TABLE estados_materia RENAME TO estados_materia_old")
    cur.execute("""
        CREATE TABLE estados_materia (
            id INTEGER NOT NULL,
            materia_id INTEGER NOT NULL,
            usuario_id INTEGER,
            estado VARCHAR(12) NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(materia_id) REFERENCES materias (id),
            FOREIGN KEY(usuario_id) REFERENCES usuarios (id),
            UNIQUE(usuario_id, materia_id)
        )
    """)
    cur.execute("""
        INSERT INTO estados_materia (id, materia_id, usuario_id, estado)
        SELECT id, materia_id, NULL, estado FROM estados_materia_old
    """)
    n = cur.rowcount
    cur.execute("DROP TABLE estados_materia_old")
    conn.commit()
    print(f"Listo. {n} filas de progreso preservadas (sin usuario asignado todavía).")
    print("Corré scripts/reclamar_progreso.py después de que el dueño original se registre.")
    conn.close()


if __name__ == "__main__":
    main()

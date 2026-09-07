"""
Asigna el progreso "huérfano" (cargado antes de que existieran los usuarios,
ver migrate_v2_usuarios.py) a una cuenta ya registrada.

Uso (con el servidor detenido, para evitar escrituras concurrentes):
    python scripts/reclamar_progreso.py tu_email@gmail.com [ruta_a_la_db]
"""
import sqlite3
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Uso: python scripts/reclamar_progreso.py tu_email@gmail.com [ruta_a_la_db]")
    sys.exit(1)

EMAIL = sys.argv[1].strip().lower()
DB_PATH = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).parent.parent / "plan_estudios.db")


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT id, email, rol FROM usuarios WHERE email = ?", (EMAIL,))
    row = cur.fetchone()
    if not row:
        print(f"No existe ningún usuario con email '{EMAIL}'. Registrate primero desde la app.")
        sys.exit(1)
    usuario_id, email, rol = row

    cur.execute("SELECT COUNT(*) FROM estados_materia WHERE usuario_id IS NULL")
    huerfanas = cur.fetchone()[0]
    if huerfanas == 0:
        print("No hay progreso huérfano para reclamar (o ya se reclamó antes).")
        return

    cur.execute("UPDATE estados_materia SET usuario_id = ? WHERE usuario_id IS NULL", (usuario_id,))
    conn.commit()
    print(f"Listo: {huerfanas} filas de progreso asignadas a {email} (id={usuario_id}, rol={rol}).")
    conn.close()


if __name__ == "__main__":
    main()

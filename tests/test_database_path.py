"""DB_PATH mueve la base (Docker la pone en un volumen); sin la variable, queda
en la raíz del repo como siempre. Se prueba en un subproceso porque la ruta se
fija al importar app.database."""
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _db_path(extra_env):
    env = {k: v for k, v in os.environ.items() if k != "DB_PATH"}
    env.update(extra_env)
    salida = subprocess.check_output(
        [sys.executable, "-c", "from app.database import DB_PATH; print(DB_PATH)"],
        cwd=str(RAIZ), env=env,
    )
    return Path(salida.decode().strip())


def test_sin_variable_la_base_queda_en_la_raiz_del_repo():
    assert _db_path({}) == RAIZ / "plan_estudios.db"


def test_con_db_path_se_usa_esa_ruta(tmp_path):
    destino = tmp_path / "otra" / "base.db"
    assert _db_path({"DB_PATH": str(destino)}) == destino

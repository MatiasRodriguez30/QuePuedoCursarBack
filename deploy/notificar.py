"""
Manda un aviso por Telegram desde los scripts de deploy (tablet_run.sh).

Uso:  python deploy/notificar.py "texto del aviso"

Siempre termina con código 0 y sin ruido: si Telegram no está configurado o no
hay red, no tiene que frenar el deploy ni el watchdog que lo llama.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import telegram_api  # noqa: E402


def main() -> int:
    texto = " ".join(sys.argv[1:]).strip()
    if texto:
        telegram_api.enviar_mensaje(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())

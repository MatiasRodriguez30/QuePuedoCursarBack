"""Muestra el chat id de quien le escribió al bot, para poner en TELEGRAM_CHAT_ID.

Uso (desde la raíz del repo, con TELEGRAM_BOT_TOKEN ya en el .env):
    python scripts/telegram_chat_id.py

1. Abrí tu bot en Telegram y mandale cualquier mensaje (por ejemplo "hola").
2. Corré este script: lista los chats que le escribieron.
3. Copiá tu número en TELEGRAM_CHAT_ID dentro del .env y reiniciá la API.

No imprime el token. Correrlo ANTES de configurar TELEGRAM_CHAT_ID: con el bot
ya andando, el servidor es quien consume los mensajes (Telegram da error 409).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config, telegram_api  # noqa: E402


def main() -> int:
    if not config.TELEGRAM_BOT_TOKEN:
        print("Falta TELEGRAM_BOT_TOKEN en el .env.")
        return 1
    try:
        updates = telegram_api.obtener_updates(None, timeout=0)
    except telegram_api.TokenInvalido:
        print("Telegram rechazó el token: revisá que esté bien copiado en el .env.")
        return 1
    except telegram_api.ConflictoPolling:
        print("Otro proceso está leyendo los mensajes del bot (¿la API ya con el bot activo?).")
        return 1
    except Exception as e:
        print("No se pudo consultar Telegram ({}).".format(e.__class__.__name__))
        return 1

    chats = {}
    for u in updates:
        chat = (u.get("message") or {}).get("chat")
        if chat and "id" in chat:
            chats[chat["id"]] = chat
    if not chats:
        print("Nadie le escribió al bot todavía. Mandale un mensaje desde Telegram y volvé a correr esto.")
        return 1
    for chat_id, chat in chats.items():
        nombre = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("title", "")
        print("chat id: {}   ({}{})".format(chat_id, nombre, " @" + chat["username"] if chat.get("username") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

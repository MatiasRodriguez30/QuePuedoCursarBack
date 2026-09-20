import asyncio

import pytest

from app import scheduler


def test_loop_ejecuta_el_envio_en_un_hilo_sin_asyncio_to_thread(monkeypatch):
    """La tablet corre Python 3.8, donde asyncio.to_thread no existe: el loop
    tiene que despachar el envío con algo que funcione ahí (run_in_executor)."""
    llamadas = []
    monkeypatch.setattr(scheduler, "enviar_recordatorios_del_dia_siguiente", lambda: llamadas.append(1))
    monkeypatch.setattr(scheduler, "_segundos_hasta_proximo_envio", lambda: 0)
    # Simula un intérprete sin to_thread (3.8): si el loop lo usara, fallaría.
    monkeypatch.delattr(asyncio, "to_thread", raising=False)

    esperas = []

    async def sleep_falso(segundos):
        esperas.append(segundos)
        if len(esperas) == 2:  # la 2da es el margen de 60s posterior al envío
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep_falso)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.loop_recordatorios())

    assert llamadas == [1]

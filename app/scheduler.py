"""
Recordatorio diario por mail de los eventos de la agenda del día siguiente.

Corre como una tarea de fondo dentro del mismo proceso de uvicorn (arrancada
en el startup de FastAPI, ver app/main.py) — no hace falta cron ni un
proceso aparte, en línea con cómo ya se maneja todo lo demás en la tablet.

Argentina no tiene horario de verano desde 2009 (Ley 26.350): la zona
America/Argentina/Buenos_Aires es UTC-3 todo el año, así que alcanza con un
offset fijo en vez de sumar una dependencia de timezones (zoneinfo no está
en la stdlib de Python 3.8, que es lo que corre la tablet).
"""
import asyncio
import logging
from datetime import datetime, timedelta

from app.database import SessionLocal
from app import models
from app.email_utils import enviar_email

logger = logging.getLogger(__name__)

OFFSET_ARGENTINA = timedelta(hours=-3)
HORA_ENVIO = 21  # 21:00 hora argentina, la noche anterior al día del recordatorio


def _ahora_argentina() -> datetime:
    return datetime.utcnow() + OFFSET_ARGENTINA


def _segundos_hasta_proximo_envio() -> float:
    ahora = _ahora_argentina()
    objetivo = ahora.replace(hour=HORA_ENVIO, minute=0, second=0, microsecond=0)
    if objetivo <= ahora:
        objetivo += timedelta(days=1)
    return (objetivo - ahora).total_seconds()


def _formatear_html(eventos: list, fecha) -> str:
    fecha_texto = fecha.strftime("%d/%m/%Y")
    filas = ""
    for e in eventos:
        horario = e.hora_inicio.strftime("%H:%M") if e.hora_inicio else "Todo el día"
        lugar = f" — {e.ubicacion}" if e.ubicacion else ""
        filas += f"<li><b>{horario}</b> — {e.titulo}{lugar}</li>"
    return (
        f"<h2>Agenda de mañana ({fecha_texto})</h2>"
        f"<ul>{filas}</ul>"
        f"<p style='color:#888;font-size:12px'>Qué Puedo Cursar — recordatorio automático</p>"
    )


def enviar_recordatorios_del_dia_siguiente() -> None:
    db = SessionLocal()
    try:
        manana = (_ahora_argentina() + timedelta(days=1)).date()
        eventos = (
            db.query(models.Evento)
            .filter(models.Evento.fecha == manana)
            .order_by(models.Evento.hora_inicio)
            .all()
        )
        if not eventos:
            logger.info("Sin eventos para %s, no se envían recordatorios.", manana)
            return

        html = _formatear_html(eventos, manana)
        usuarios = db.query(models.Usuario).all()
        for usuario in usuarios:
            enviar_email(usuario.email, f"Recordatorio: agenda de mañana ({manana.strftime('%d/%m')})", html)
        logger.info("Recordatorios de %s enviados a %d usuarios.", manana, len(usuarios))
    finally:
        db.close()


async def loop_recordatorios():
    while True:
        espera = _segundos_hasta_proximo_envio()
        await asyncio.sleep(espera)
        try:
            enviar_recordatorios_del_dia_siguiente()
        except Exception:
            logger.exception("Error enviando recordatorios diarios")
        # Margen para no re-disparar dos veces si el reloj cae justo en el borde.
        await asyncio.sleep(60)

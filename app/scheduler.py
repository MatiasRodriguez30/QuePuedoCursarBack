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

from sqlalchemy import or_

from app.database import SessionLocal
from app import config, models
from app.email_utils import enviar_email
from app.telegram_api import enviar_mensaje as enviar_telegram

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


def _formatear_texto(eventos: list, titulo: str) -> str:
    """Versión en texto plano de la agenda, para Telegram."""
    lineas = [titulo]
    for e in eventos:
        horario = e.hora_inicio.strftime("%H:%M") if e.hora_inicio else "Todo el día"
        lugar = f" ({e.ubicacion})" if e.ubicacion else ""
        marca = " [personal]" if e.personal else ""
        lineas.append(f"• {horario} — {e.titulo}{lugar}{marca}")
    return "\n".join(lineas)


def eventos_visibles(db, fecha, usuario_id) -> list:
    """Eventos de `fecha` que ve ese usuario: los institucionales más sus
    propios personales (nunca los personales de otro). `usuario_id` None =
    sólo institucionales."""
    cond = models.Evento.personal == False  # noqa: E712
    if usuario_id is not None:
        cond = or_(cond, models.Evento.creado_por_id == usuario_id)
    eventos = db.query(models.Evento).filter(models.Evento.fecha == fecha).filter(cond).all()
    return sorted(eventos, key=lambda e: (e.hora_inicio is not None, e.hora_inicio))


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

        # Institucionales van en el mail de todos. Los personales sólo en el
        # de quien los creó: antes se mandaba la misma lista completa a todo
        # el mundo, mezclando el evento personal de un usuario en el mail de
        # cualquier otro.
        institucionales = [e for e in eventos if not e.personal]
        personales_por_usuario = {}
        for e in eventos:
            if e.personal and e.creado_por_id is not None:
                personales_por_usuario.setdefault(e.creado_por_id, []).append(e)

        usuarios = db.query(models.Usuario).all()
        asunto = f"Recordatorio: agenda de mañana ({manana.strftime('%d/%m')})"
        enviados = 0
        for u in usuarios:
            propios = institucionales + personales_por_usuario.get(u.id, [])
            if not propios:
                continue  # nada suyo para mañana: no le mandamos un mail vacío
            propios.sort(key=lambda e: (e.hora_inicio is not None, e.hora_inicio))
            if enviar_email(u.email, asunto, _formatear_html(propios, manana)):
                enviados += 1
            # El bot de Telegram es de una sola cuenta (la del dueño): sólo a
            # ella le llega, con lo suyo, igual que el mail.
            if config.TELEGRAM_USUARIO_EMAIL and u.email == config.TELEGRAM_USUARIO_EMAIL.strip().lower():
                enviar_telegram(_formatear_texto(propios, f"Agenda de mañana ({manana.strftime('%d/%m')})"))
        logger.info("Recordatorios de %s: %d/%d mails enviados.", manana, enviados, len(usuarios))
    finally:
        db.close()


async def loop_recordatorios():
    while True:
        espera = _segundos_hasta_proximo_envio()
        await asyncio.sleep(espera)
        try:
            # enviar_recordatorios_del_dia_siguiente() hace llamadas HTTP
            # bloqueantes (urllib) para mandar los mails; llamarla directo acá
            # congelaría TODA la API (HTTP y WebSockets) mientras dura el
            # envío. run_in_executor la corre en un hilo aparte sin bloquear el
            # loop (asyncio.to_thread sería lo mismo, pero es 3.9+ y la tablet
            # corre 3.8: ahí tiraba AttributeError y el mail nunca salía).
            await asyncio.get_event_loop().run_in_executor(None, enviar_recordatorios_del_dia_siguiente)
        except Exception:
            logger.exception("Error enviando recordatorios diarios")
        # Margen para no re-disparar dos veces si el reloj cae justo en el borde.
        await asyncio.sleep(60)

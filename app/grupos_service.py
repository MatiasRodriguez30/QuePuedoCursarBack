"""Lógica de grupos: códigos, datos que se envían al frontend y eventos WebSocket.

Compatible con Python 3.8 (la tablet): sin `X | None`, sin `list[...]` como
anotación, sin `asyncio.to_thread`.
"""
import secrets
import time
from datetime import datetime
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from app.ws_manager import manager

# Sin caracteres que se confunden al dictar/leer (0/O, 1/I/L).
ALFABETO_CODIGO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LARGO_CODIGO = 8

# Si alguien marca y desmarca una materia (Deshacer/Rehacer), el grupo no
# recibe el mismo aviso una y otra vez.
VENTANA_ANTI_SPAM_SEG = 60
_anuncios_recientes: Dict[Tuple[int, int, str], float] = {}


def generar_codigo(db: Session) -> str:
    for _ in range(20):
        codigo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(LARGO_CODIGO))
        if not db.query(models.Grupo).filter(models.Grupo.codigo == codigo).first():
            return codigo
    raise RuntimeError("No se pudo generar un código de grupo único")


def miembros_payload(grupo: models.Grupo) -> List[dict]:
    en_linea = manager.usuarios_en_linea()
    miembros = sorted(grupo.miembros, key=lambda m: m.id)
    return [
        {
            "usuario_id": m.usuario_id,
            "apodo": m.usuario.apodo,
            "comparte": bool(m.comparte),
            "en_linea": m.usuario_id in en_linea,
        }
        for m in miembros
    ]


def grupo_payload(grupo: models.Grupo, usuario: models.Usuario) -> dict:
    yo = next((m for m in grupo.miembros if m.usuario_id == usuario.id), None)
    return {
        "id": grupo.id,
        "nombre": grupo.nombre,
        "codigo": grupo.codigo,
        "creado_en": grupo.creado_en,
        "yo": {
            "usuario_id": usuario.id,
            "apodo": usuario.apodo,
            "comparte": bool(yo.comparte) if yo else True,
        },
        "miembros": miembros_payload(grupo),
    }


async def emitir_miembros(grupo: models.Grupo) -> None:
    """Avisa a todos los miembros conectados del grupo que cambió la lista
    (alguien entró/salió, cambió su apodo o su preferencia, o se
    conectó/desconectó)."""
    ids = [m.usuario_id for m in grupo.miembros]
    await manager.enviar_a_usuarios(
        ids,
        "grupo_miembros",
        {"grupo_id": grupo.id, "miembros": miembros_payload(grupo)},
    )


async def notificar_presencia(usuario_id: int) -> None:
    """Se llama cuando el usuario abre su primera conexión o cierra la última."""
    with SessionLocal() as db:
        membresia = db.query(models.Membresia).filter(models.Membresia.usuario_id == usuario_id).first()
        if membresia is not None:
            await emitir_miembros(membresia.grupo)


async def anunciar_logro(
    usuario: models.Usuario,
    materia: models.Materia,
    estado_previo,
    estado_nuevo,
) -> None:
    """Si el usuario aprobó o regularizó una materia, avisa al resto de su
    grupo. Sólo participa quien tiene `comparte` activado (envía y recibe)."""
    if estado_nuevo not in (models.EstadoEnum.PROMOCIONADA, models.EstadoEnum.REGULAR):
        return
    if estado_previo == estado_nuevo:
        return
    membresia = usuario.membresia
    if membresia is None or not membresia.comparte:
        return

    ahora = time.monotonic()
    for clave in [c for c, t in _anuncios_recientes.items() if ahora - t > VENTANA_ANTI_SPAM_SEG]:
        _anuncios_recientes.pop(clave, None)
    clave = (usuario.id, materia.id, estado_nuevo.value)
    if clave in _anuncios_recientes:
        return
    _anuncios_recientes[clave] = ahora

    destinatarios = [
        m.usuario_id
        for m in membresia.grupo.miembros
        if m.usuario_id != usuario.id and m.comparte
    ]
    await manager.enviar_a_usuarios(
        destinatarios,
        "logro_grupo",
        {
            "usuario_id": usuario.id,
            "apodo": usuario.apodo,
            "materia_id": materia.id,
            "materia_nombre": materia.nombre,
            "estado": estado_nuevo.value,
            "en": datetime.utcnow().isoformat() + "Z",
        },
    )

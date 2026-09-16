import asyncio
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session

from app import auth, models
from app.database import engine, get_db
from app.ws_manager import manager
from app.scheduler import loop_recordatorios
from app.routers import materias, prerequisitos, estados, consultas, config, eventos, carreras, usuarios, auth as auth_router

# Crea las tablas si no existen
models.Base.metadata.create_all(bind=engine)

# ─── Rate limiter (FIX 4) ─────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ─── CORS (FIX 3): lista explícita de orígenes permitidos ────────────────────
# Wildcard reemplazado por lista fija + extensión opcional via variable de entorno
# EXTRA_CORS_ORIGINS (separada por comas) para deploys adicionales.
ALLOWED_ORIGINS = [
    "https://app.takana.online",
    "http://localhost:5173",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
]
extra = os.getenv("EXTRA_CORS_ORIGINS", "")
if extra:
    ALLOWED_ORIGINS += [o.strip() for o in extra.split(",") if o.strip()]

app = FastAPI(
    title="Qué puedo cursar",
    description=(
        "API REST + WebSocket para gestionar el plan de estudios universitario.\n\n"
        "## WebSocket\n"
        "Conectate a `ws://<host>/ws?token=<bearer_token>` para recibir en tiempo real "
        "todos los cambios (estados, materias, prerequisitos). Se requiere autenticación "
        "via el query param `?token=`. Múltiples clientes pueden conectarse simultáneamente.\n\n"
        "### Eventos emitidos\n"
        "| Evento | Descripción |\n"
        "|---|---|\n"
        "| `materia_creada` | Nueva materia agregada |\n"
        "| `materia_actualizada` | Materia modificada |\n"
        "| `materia_eliminada` | Materia eliminada |\n"
        "| `prerequisito_creado` | Nuevo prerequisito |\n"
        "| `prerequisito_eliminado` | Prerequisito eliminado |\n"
        "| `estado_actualizado` | Estado de materia cambiado |\n"
        "| `evento_creado` / `evento_actualizado` / `evento_eliminado` | Cambios en la agenda |\n"
        "| `carrera_creada` / `carrera_actualizada` / `carrera_eliminada` | Cambios en las carreras disponibles |\n"
    ),
    version="1.0.0",
)

# Registrar el limiter y el handler de 429 en la app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers REST
app.include_router(auth_router.router)
app.include_router(materias.router)
app.include_router(prerequisitos.router)
app.include_router(estados.router)
app.include_router(consultas.router)
app.include_router(config.router)
app.include_router(eventos.router)
app.include_router(carreras.router)
app.include_router(usuarios.router)


@app.on_event("startup")
async def iniciar_scheduler():
    asyncio.create_task(loop_recordatorios())


# ─── WebSocket (FIX 2) ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Endpoint WebSocket autenticado.

    Parámetros de conexión:
    - **token** (query param, requerido): Bearer token de sesión obtenido en /auth/login.
      Ejemplo: ws://<host>/ws?token=<tu_token>

    Si el token falta o es inválido/expirado, la conexión se cierra con código 4001.
    Una vez conectado, el cliente puede enviar cualquier texto (ej. ping) para
    mantener la conexión viva. El servidor emite eventos JSON:
    {"event": "<nombre_evento>", "data": {...}}
    """
    usuario = auth.get_user_from_token(token, db)
    if usuario is None:
        await websocket.close(code=4001)
        return

    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # mantener la conexión activa
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    """El frontend (React) vive en un repo/deploy aparte (Vercel).
    Esta ruta sólo confirma que la API está viva."""
    return {"status": "ok", "api": "Qué puedo cursar", "docs": "/docs"}


@app.get("/health", tags=["Root"])
def health():
    return {
        "status": "ok",
        "docs": "/docs",
        "websocket": "/ws",
        "clientes_ws_conectados": len(manager.active_connections),
    }

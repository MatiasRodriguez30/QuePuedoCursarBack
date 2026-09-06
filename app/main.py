from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine
from app import models
from app.ws_manager import manager
from app.routers import materias, prerequisitos, estados, consultas, config

# Crea las tablas si no existen
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Qué puedo cursar",
    description=(
        "API REST + WebSocket para gestionar el plan de estudios universitario.\n\n"
        "## WebSocket\n"
        "Conectate a `ws://<host>/ws` para recibir en tiempo real todos los cambios "
        "(estados, materias, prerequisitos). Múltiples clientes pueden conectarse simultáneamente.\n\n"
        "### Eventos emitidos\n"
        "| Evento | Descripción |\n"
        "|---|---|\n"
        "| `materia_creada` | Nueva materia agregada |\n"
        "| `materia_actualizada` | Materia modificada |\n"
        "| `materia_eliminada` | Materia eliminada |\n"
        "| `prerequisito_creado` | Nuevo prerequisito |\n"
        "| `prerequisito_eliminado` | Prerequisito eliminado |\n"
        "| `estado_actualizado` | Estado de materia cambiado |\n"
    ),
    version="1.0.0",
)

# CORS abierto: el frontend (React, en Vercel) es un origen distinto al del
# backend (expuesto vía Cloudflare Tunnel desde la tablet), así que no hay
# una lista fija de orígenes confiables para restringir.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers REST
app.include_router(materias.router)
app.include_router(prerequisitos.router)
app.include_router(estados.router)
app.include_router(consultas.router)
app.include_router(config.router)


# ─── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Endpoint WebSocket. Conectarse aquí para recibir actualizaciones en tiempo real.
    El cliente puede enviar cualquier texto (ej. ping) para mantener la conexión viva.
    """
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

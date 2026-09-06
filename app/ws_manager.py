import json
from typing import List
from fastapi import WebSocket


class ConnectionManager:
    """Maneja todas las conexiones WebSocket activas y permite hacer broadcast."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"[WS] Cliente conectado. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"[WS] Cliente desconectado. Total: {len(self.active_connections)}")

    async def broadcast(self, event: str, data: dict):
        """Envía un evento a todos los clientes conectados."""
        message = json.dumps({"event": event, "data": data}, default=str)
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for conn in dead:
            self.disconnect(conn)


# Instancia singleton compartida por toda la app
manager = ConnectionManager()

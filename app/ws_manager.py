import json
from typing import Dict, Iterable, List, Optional, Set
from fastapi import WebSocket


class ConnectionManager:
    """Maneja las conexiones WebSocket activas.

    - `broadcast` manda a TODOS (sólo para datos compartidos: materias, agenda,
      carreras, configuración).
    - `enviar_a_usuarios` manda únicamente a los dispositivos de esos usuarios
      (progreso propio, eventos del grupo).
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        # Cada conexión recuerda de qué usuario es (un usuario puede tener
        # varias: tablet, celular, otra pestaña).
        self._usuario_de: Dict[WebSocket, Optional[int]] = {}

    async def connect(self, websocket: WebSocket, usuario_id: Optional[int] = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        self._usuario_de[websocket] = usuario_id
        print(f"[WS] Cliente conectado. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> Optional[int]:
        """Quita la conexión y devuelve el id del usuario al que pertenecía."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        usuario_id = self._usuario_de.pop(websocket, None)
        print(f"[WS] Cliente desconectado. Total: {len(self.active_connections)}")
        return usuario_id

    def usuarios_en_linea(self) -> Set[int]:
        return {uid for uid in self._usuario_de.values() if uid is not None}

    def esta_en_linea(self, usuario_id: int) -> bool:
        return usuario_id in self.usuarios_en_linea()

    async def _enviar(self, conexiones: Iterable[WebSocket], message: str):
        dead = []
        # Copia de la lista: un disconnect() concurrente durante el await de
        # abajo (otro cliente cerrando conexión a la vez) no debe mutar la
        # lista mientras la estamos recorriendo.
        for connection in list(conexiones):
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for conn in dead:
            self.disconnect(conn)

    async def broadcast(self, event: str, data: dict):
        """Envía un evento a todos los clientes conectados."""
        message = json.dumps({"event": event, "data": data}, default=str)
        await self._enviar(self.active_connections, message)

    async def enviar_a_usuarios(self, usuario_ids: Iterable[int], event: str, data: dict):
        """Envía un evento sólo a las conexiones de los usuarios indicados."""
        ids = set(usuario_ids)
        if not ids:
            return
        message = json.dumps({"event": event, "data": data}, default=str)
        destino = [c for c in self.active_connections if self._usuario_de.get(c) in ids]
        await self._enviar(destino, message)


# Instancia singleton compartida por toda la app
manager = ConnectionManager()

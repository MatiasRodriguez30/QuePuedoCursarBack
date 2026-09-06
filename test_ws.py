import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

async def main():
    import websockets

    print("=== TEST WEBSOCKET ===")
    async with websockets.connect("ws://localhost:8000/ws") as ws:
        print("[OK] Conexion WebSocket establecida")

        # Obtener primera materia disponible
        with urllib.request.urlopen("http://localhost:8000/materias") as r:
            mats = json.loads(r.read())
        mid = mats[0]["id"]
        nombre = mats[0]["nombre"]
        print(f"Usando materia: '{nombre}' (id={mid})")

        # Trigger: cambiar estado -> broadcast
        body = json.dumps({"estado": "REGULAR"}).encode()
        req = urllib.request.Request(
            f"http://localhost:8000/estados/{mid}", data=body,
            headers={"Content-Type": "application/json"}, method="PUT"
        )
        with urllib.request.urlopen(req) as r:
            print(f"PUT /estados/{mid} -> HTTP {r.status}")

        # Esperar broadcast
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=4)
            event = json.loads(msg)
            print(f"[OK] Broadcast recibido!")
            print(f"   event  : {event['event']}")
            print(f"   materia: {event['data'].get('materia', {}).get('nombre', '?')}")
            print(f"   estado : {event['data'].get('estado', '?')}")
        except asyncio.TimeoutError:
            print("[FAIL] Timeout -- no se recibio broadcast")

        # Segundo test: crear materia -> broadcast materia_creada
        body2 = json.dumps({"codigo": "WS_NEW", "nombre": "Materia desde WS test"}).encode()
        req2 = urllib.request.Request(
            "http://localhost:8000/materias", data=body2,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req2) as r:
            m = json.loads(r.read())
            print(f"\nCreada materia id={m['id']} via REST")

        try:
            msg2 = await asyncio.wait_for(ws.recv(), timeout=4)
            e2 = json.loads(msg2)
            print(f"[OK] Broadcast recibido!")
            print(f"   event  : {e2['event']}")
            print(f"   nombre : {e2['data'].get('nombre', '?')}")
        except asyncio.TimeoutError:
            print("[FAIL] Timeout -- no se recibio broadcast de materia_creada")

    print("\n=== FIN TEST ===")

asyncio.run(main())

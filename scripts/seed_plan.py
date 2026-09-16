"""
Carga el plan de estudios de Ingeniería en Sistemas (UTN, Plan 2023) + las
electivas del 2do semestre 2026 en una instancia recién levantada del backend.

===============================================================================
USO
===============================================================================
1. Asegurate de que el usuario admin ya existe (registrate o usá make_admin.py).

2. Definí las credenciales admin (recomendado via variables de entorno):
       export ADMIN_EMAIL=l390585@gmail.com
       export ADMIN_PASSWORD=mi_contrasena

   O simplemente corré el script y te las pedirá de forma interactiva.

3. Arrancá el servidor:
       uvicorn app.main:app --host 0.0.0.0 --port 8000

4. En otra terminal:
       python scripts/seed_plan.py [URL_BASE]

   URL_BASE es opcional, por defecto http://localhost:8000

Es seguro correrlo sobre una base vacía. Si ya hay materias cargadas, primero
las borra todas (junto con sus prerequisitos, por cascada) para evitar
duplicados.
===============================================================================
"""
import getpass
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
DATA_FILE = Path(__file__).parent / "plan_data.json"


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8")
        raise RuntimeError("{} {} -> {}: {}".format(method, path, e.code, detail))


def obtener_token():
    """Obtiene el token de sesión admin: primero intenta con env vars,
    luego pide las credenciales de forma interactiva."""
    email = os.getenv("ADMIN_EMAIL", "")
    password = os.getenv("ADMIN_PASSWORD", "")

    if not email:
        email = input("Email admin: ").strip()
    if not password:
        password = getpass.getpass("Password admin: ")

    print("Autenticando como {}...".format(email))
    try:
        resp = call("POST", "/auth/login", {"email": email, "password": password})
    except RuntimeError as e:
        print("[ERROR] Fallo el login: {}".format(e))
        sys.exit(1)

    token = resp.get("token")
    if not token:
        print("[ERROR] La respuesta no incluye token. Respuesta: {}".format(resp))
        sys.exit(1)

    print("Autenticado correctamente. Token: {}...".format(token[:10]))
    return token


def main():
    token = obtener_token()
    plan = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    existentes = call("GET", "/materias")
    if existentes:
        print("Borrando {} materias existentes...".format(len(existentes)))
        for m in existentes:
            call("DELETE", "/materias/{}".format(m["id"]), token=token)

    id_por_codigo = {}

    for m in plan["core"]:
        creada = call("POST", "/materias", {
            "codigo": m["codigo"],
            "nombre": m["nombre"],
            "anio": m["anio"],
            "cuatrimestre": m["cuatrimestre"],
            "horas_semanales": m.get("horas_semanales"),
            "es_basica_compartida": m.get("basica", False),
        }, token=token)
        id_por_codigo[m["codigo"]] = creada["id"]
    print("Creadas {} materias del plan de estudios.".format(len(plan["core"])))

    for m in plan["electivas"]:
        creada = call("POST", "/materias", {
            "codigo": m["codigo"],
            "nombre": m["nombre"],
            "descripcion": m.get("descripcion"),
            "anio": m["anio"],
            "cuatrimestre": m["cuatrimestre"],
            "horas_semanales": m.get("horas_semanales"),
        }, token=token)
        id_por_codigo[m["codigo"]] = creada["id"]
    print("Creadas {} electivas.".format(len(plan["electivas"])))

    total_prereq = 0
    for m in plan["core"] + plan["electivas"]:
        materia_id = id_por_codigo[m["codigo"]]
        for req_codigo in m["reg"]:
            call("POST", "/prerequisitos", {
                "materia_id": materia_id,
                "materia_requerida_id": id_por_codigo[req_codigo],
                "tipo": "REGULARIZADA",
            }, token=token)
            total_prereq += 1
        for req_codigo in m["aprob"]:
            call("POST", "/prerequisitos", {
                "materia_id": materia_id,
                "materia_requerida_id": id_por_codigo[req_codigo],
                "tipo": "APROBADA",
            }, token=token)
            total_prereq += 1
    print("Creados {} prerequisitos.".format(total_prereq))
    print("\nListo. Total materias en BD:", len(call("GET", "/materias")))


if __name__ == "__main__":
    main()

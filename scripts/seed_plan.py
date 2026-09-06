"""
Carga el plan de estudios de Ingeniería en Sistemas (UTN, Plan 2023) + las
electivas del 2do semestre 2026 en una instancia recién levantada del backend.

Uso:
    1. Arrancar el servidor:  uvicorn app.main:app --host 0.0.0.0 --port 8000
    2. En otra terminal:      python scripts/seed_plan.py [URL_BASE]

    URL_BASE es opcional, por defecto http://localhost:8000

Es seguro correrlo sobre una base vacía. Si ya hay materias cargadas, primero
las borra todas (junto con sus prerequisitos, por cascada) para evitar
duplicados.
"""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
DATA_FILE = Path(__file__).parent / "plan_data.json"


def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8")
        raise RuntimeError(f"{method} {path} -> {e.code}: {detail}")


def main():
    plan = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    existentes = call("GET", "/materias")
    if existentes:
        print(f"Borrando {len(existentes)} materias existentes...")
        for m in existentes:
            call("DELETE", f"/materias/{m['id']}")

    id_por_codigo = {}

    for m in plan["core"]:
        creada = call("POST", "/materias", {
            "codigo": m["codigo"],
            "nombre": m["nombre"],
            "anio": m["anio"],
            "cuatrimestre": m["cuatrimestre"],
            "horas_semanales": m.get("horas_semanales"),
            "es_basica_compartida": m.get("basica", False),
        })
        id_por_codigo[m["codigo"]] = creada["id"]
    print(f"Creadas {len(plan['core'])} materias del plan de estudios.")

    for m in plan["electivas"]:
        creada = call("POST", "/materias", {
            "codigo": m["codigo"],
            "nombre": m["nombre"],
            "descripcion": m.get("descripcion"),
            "anio": m["anio"],
            "cuatrimestre": m["cuatrimestre"],
            "horas_semanales": m.get("horas_semanales"),
        })
        id_por_codigo[m["codigo"]] = creada["id"]
    print(f"Creadas {len(plan['electivas'])} electivas.")

    total_prereq = 0
    for m in plan["core"] + plan["electivas"]:
        materia_id = id_por_codigo[m["codigo"]]
        for req_codigo in m["reg"]:
            call("POST", "/prerequisitos", {
                "materia_id": materia_id,
                "materia_requerida_id": id_por_codigo[req_codigo],
                "tipo": "REGULARIZADA",
            })
            total_prereq += 1
        for req_codigo in m["aprob"]:
            call("POST", "/prerequisitos", {
                "materia_id": materia_id,
                "materia_requerida_id": id_por_codigo[req_codigo],
                "tipo": "APROBADA",
            })
            total_prereq += 1
    print(f"Creados {total_prereq} prerequisitos.")
    print("\nListo. Total materias en BD:", len(call("GET", "/materias")))


if __name__ == "__main__":
    main()

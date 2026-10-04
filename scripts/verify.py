"""Verificación completa antes de pedir un merge.

Uso (desde la raíz del repo):
    venv\\Scripts\\python.exe scripts\\verify.py

1. Busca en el código construcciones que NO funcionan en Python 3.8 (la
   versión de la tablet de producción) aunque los tests locales pasen en una
   versión más nueva: `X | None` y `list[int]` en anotaciones, `match`,
   `asyncio.to_thread`, `str.removeprefix/removesuffix`, `zoneinfo`,
   `datetime.UTC`, etc.
2. Corre la suite completa de tests.

La CI de GitHub además corre los tests en un Python 3.8 real: esta
verificación local es la primera barrera, no la única.
Termina con código 0 solo si todo pasa. Pegá la salida completa en el reporte.
"""
import ast
import pathlib
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CARPETAS = ("app", "deploy", "scripts", "tests")

GENERICOS_NUEVOS = {"list", "dict", "tuple", "set", "frozenset", "type"}
ATRIBUTOS_NUEVOS = {
    "to_thread": "asyncio.to_thread no existe en 3.8: usar loop.run_in_executor",
    "removeprefix": "str.removeprefix no existe en 3.8",
    "removesuffix": "str.removesuffix no existe en 3.8",
    "UTC": "datetime.UTC no existe en 3.8: usar timezone.utc",
}
MODULOS_NUEVOS = {"zoneinfo": "zoneinfo no existe en 3.8", "graphlib": "graphlib no existe en 3.8", "tomllib": "tomllib no existe en 3.8"}


def _anotacion_incompatible(nodo):
    for sub in ast.walk(nodo):
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
            return "unión con '|' en una anotación (usar Optional[...] / Union[...])"
        if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) and sub.value.id in GENERICOS_NUEVOS:
            return "'{}[...]' en una anotación (usar List/Dict/Tuple/Set de typing)".format(sub.value.id)
    return None


def problemas_py38(archivo):
    fuente = archivo.read_text(encoding="utf-8-sig")  # algunos archivos de Windows traen BOM
    problemas = []
    try:
        arbol = ast.parse(fuente, filename=str(archivo), feature_version=(3, 8))
    except SyntaxError as e:
        problemas.append("{}: sintaxis inválida para 3.8: {}".format(e.lineno, e.msg))
        try:
            arbol = ast.parse(fuente, filename=str(archivo))  # seguir buscando el resto de los problemas
        except SyntaxError:
            return problemas

    for nodo in ast.walk(arbol):
        anotaciones = []
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = nodo.args
            for a in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
                if a is not None and a.annotation is not None:
                    anotaciones.append(a.annotation)
            if nodo.returns is not None:
                anotaciones.append(nodo.returns)
        elif isinstance(nodo, ast.AnnAssign):
            anotaciones.append(nodo.annotation)
        for anotacion in anotaciones:
            motivo = _anotacion_incompatible(anotacion)
            if motivo:
                problemas.append("{}: {}".format(anotacion.lineno, motivo))

        if type(nodo).__name__ == "Match":
            problemas.append("{}: 'match/case' no existe en 3.8".format(nodo.lineno))
        if isinstance(nodo, ast.Attribute) and nodo.attr in ATRIBUTOS_NUEVOS:
            problemas.append("{}: {}".format(nodo.lineno, ATRIBUTOS_NUEVOS[nodo.attr]))
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                raiz = alias.name.split(".")[0]
                if raiz in MODULOS_NUEVOS:
                    problemas.append("{}: {}".format(nodo.lineno, MODULOS_NUEVOS[raiz]))
        if isinstance(nodo, ast.ImportFrom) and nodo.module:
            raiz = nodo.module.split(".")[0]
            if raiz in MODULOS_NUEVOS:
                problemas.append("{}: {}".format(nodo.lineno, MODULOS_NUEVOS[raiz]))
            if nodo.module == "datetime" and any(a.name == "UTC" for a in nodo.names):
                problemas.append("{}: {}".format(nodo.lineno, ATRIBUTOS_NUEVOS["UTC"]))
    return problemas


def main():
    print("== 1/2 Compatibilidad con Python 3.8 ==")
    total = 0
    for carpeta in CARPETAS:
        for archivo in sorted((RAIZ / carpeta).rglob("*.py")):
            for p in problemas_py38(archivo):
                print("  {}:{}".format(archivo.relative_to(RAIZ), p))
                total += 1
    print("  OK" if total == 0 else "  {} problema(s)".format(total))

    print("== 2/2 Tests ==")
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=str(RAIZ))

    ok = total == 0 and tests.returncode == 0
    print("\nVERIFICACION: {}".format("OK" if ok else "FALLO"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

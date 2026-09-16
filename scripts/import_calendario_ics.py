"""
Importa (una sola vez) el calendario institucional de UTN desde un archivo
.ics exportado de Google Calendar a la tabla `eventos`, como snapshot fijo
(sin sincronización en vivo — para eso habría que correr este script de
nuevo con un .ics actualizado).

Parser propio y minimalista (sin agregar la librería `icalendar` como
dependencia): sólo soporta los campos y formatos de fecha que efectivamente
aparecen en este calendario (ver notas abajo). No expande RRULE — se
comprobó que todas las series recurrentes de este archivo ya terminaron
(UNTIL en el pasado), así que sólo importar el DTSTART de cada VEVENT no
pierde ninguna fecha futura.

Formatos de DTSTART soportados:
  - `;VALUE=DATE:YYYYMMDD`                 -> evento de todo el día
  - `:YYYYMMDDTHHMMSSZ`                    -> UTC, se convierte a hora local
  - `;TZID=America/...:YYYYMMDDTHHMMSS`    -> ya es hora local (Argentina no
    tiene horario de verano desde 2009, así que toda TZID en este archivo es
    UTC-3 fijo, sin importar cuál de las dos zonas diga exactamente).

Es seguro correr este script más de una vez: usa el UID de cada VEVENT
(columna `uid_ics`, única) para no duplicar eventos ya importados.

Uso:
    python scripts/import_calendario_ics.py archivo.ics
"""
import html
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app import models

OFFSET_ARGENTINA = timedelta(hours=-3)

_RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_RE_CIERRE_PARRAFO = re.compile(r"</p>", re.IGNORECASE)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_SALTOS_DE_MAS = re.compile(r"\n{3,}")


def limpiar_html(texto: str) -> str:
    """Algunas DESCRIPTION de este calendario (las cargadas a mano por
    Bedelía/Secretaría, a diferencia de las generadas por Google) traen
    HTML crudo en vez de texto plano (`<p>`, `<b>`, `&nbsp;`) — sin esto,
    se mostraban los tags literales en la agenda y encima cortaban el
    texto real (qué materias entran en cada mesa de examen)."""
    if not texto:
        return texto
    texto = _RE_BR.sub("\n", texto)
    texto = _RE_CIERRE_PARRAFO.sub("\n", texto)
    texto = _RE_TAG.sub("", texto)
    texto = html.unescape(texto).replace("\xa0", " ")
    texto = "\n".join(linea.rstrip() for linea in texto.split("\n"))
    # <p><br></p> (separador vacío entre secciones) puede generar 3+ saltos
    # seguidos; los dejamos en máximo un renglón en blanco.
    texto = _RE_SALTOS_DE_MAS.sub("\n\n", texto)
    return texto.strip()


def desplegar_lineas(texto: str):
    """RFC5545: una línea de continuación empieza con un espacio o tab y hay
    que unirla a la anterior (si no, DESCRIPTION largo queda cortado)."""
    lineas = texto.replace("\r\n", "\n").split("\n")
    resultado = []
    for linea in lineas:
        if linea.startswith(" ") or linea.startswith("\t"):
            if resultado:
                resultado[-1] += linea[1:]
        else:
            resultado.append(linea)
    return resultado


def desescapar(valor: str) -> str:
    return (
        valor.replace("\\n", "\n").replace("\\N", "\n")
        .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    )


def parsear_dtstart(linea: str):
    """Devuelve (fecha, hora_o_None) a partir de una línea DTSTART completa."""
    clave, valor = linea.split(":", 1)

    if "VALUE=DATE" in clave:
        d = datetime.strptime(valor, "%Y%m%d").date()
        return d, None

    if valor.endswith("Z"):
        dt_utc = datetime.strptime(valor, "%Y%m%dT%H%M%SZ")
        dt_local = dt_utc + OFFSET_ARGENTINA
        return dt_local.date(), dt_local.time()

    # TZID=America/... (Buenos Aires o Araguaina, ambas UTC-3 fijo en este archivo)
    dt = datetime.strptime(valor, "%Y%m%dT%H%M%S")
    return dt.date(), dt.time()


def parsear_eventos(contenido: str):
    lineas = desplegar_lineas(contenido)
    eventos = []
    actual = None

    for linea in lineas:
        if linea == "BEGIN:VEVENT":
            actual = {}
        elif linea == "END:VEVENT":
            if actual and "fecha" in actual and "titulo" in actual:
                eventos.append(actual)
            actual = None
        elif actual is not None:
            if linea.startswith("SUMMARY:"):
                actual["titulo"] = desescapar(linea[len("SUMMARY:"):])
            elif linea.startswith("DESCRIPTION:"):
                actual["descripcion"] = limpiar_html(desescapar(linea[len("DESCRIPTION:"):]))
            elif linea.startswith("LOCATION:"):
                actual["ubicacion"] = desescapar(linea[len("LOCATION:"):]) or None
            elif linea.startswith("UID:"):
                actual["uid_ics"] = linea[len("UID:"):]
            elif linea.startswith("DTSTART"):
                fecha, hora = parsear_dtstart(linea)
                actual["fecha"] = fecha
                actual["hora_inicio"] = hora

    return eventos


def main():
    if len(sys.argv) < 2:
        print("Uso: python scripts/import_calendario_ics.py archivo.ics")
        sys.exit(1)

    ruta = Path(sys.argv[1])
    contenido = ruta.read_text(encoding="utf-8")
    eventos = parsear_eventos(contenido)
    print(f"Parseados {len(eventos)} eventos del archivo.")

    db = SessionLocal()
    try:
        existentes = {
            uid for (uid,) in db.query(models.Evento.uid_ics).filter(models.Evento.uid_ics.isnot(None))
        }
        vistos_en_esta_corrida = set()
        nuevos = 0
        for ev in eventos:
            uid = ev.get("uid_ics")
            # Google exporta instancias modificadas de eventos recurrentes
            # reusando el mismo UID del maestro (con RECURRENCE-ID aparte, que
            # este parser no lee) -> el mismo UID puede repetirse en el archivo.
            # Nos quedamos con la primera ocurrencia y saltamos el resto.
            if uid and (uid in existentes or uid in vistos_en_esta_corrida):
                continue
            if uid:
                vistos_en_esta_corrida.add(uid)
            db.add(models.Evento(
                titulo=ev["titulo"],
                descripcion=ev.get("descripcion"),
                ubicacion=ev.get("ubicacion"),
                fecha=ev["fecha"],
                hora_inicio=ev.get("hora_inicio"),
                origen=models.OrigenEvento.IMPORTADO,
                uid_ics=uid,
            ))
            nuevos += 1
        db.commit()
        print(f"Insertados {nuevos} eventos nuevos ({len(eventos) - nuevos} ya existían).")
    finally:
        db.close()


if __name__ == "__main__":
    main()

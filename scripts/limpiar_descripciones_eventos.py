"""
Limpieza única de las descripciones de eventos ya importados: algunas venían
con HTML crudo (`<p>`, `<b>`, `&nbsp;`) en vez de texto plano, cargadas así
directamente por Bedelía/Secretaría en el calendario de Google (a diferencia
de las que genera Google mismo, que sí vienen en texto plano). Reutiliza
`limpiar_html` de import_calendario_ics.py — ver ese archivo para el porqué.

Es seguro correr este script más de una vez (limpiar texto ya limpio no
cambia nada).

Uso:
    python scripts/limpiar_descripciones_eventos.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import SessionLocal
from app import models
from import_calendario_ics import limpiar_html


def main():
    db = SessionLocal()
    try:
        eventos = db.query(models.Evento).filter(models.Evento.descripcion.isnot(None)).all()
        cambiados = 0
        for ev in eventos:
            limpia = limpiar_html(ev.descripcion)
            if limpia != ev.descripcion:
                ev.descripcion = limpia
                cambiados += 1
        db.commit()
        print(f"Revisados {len(eventos)} eventos con descripción, limpiados {cambiados}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

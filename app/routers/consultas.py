from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/consultas", tags=["Consultas"])


def _estados_map(db: Session) -> dict:
    """Construye un dict {materia_id: EstadoEnum} para consultas rápidas."""
    return {
        e.materia_id: e.estado
        for e in db.query(models.EstadoMateria).all()
    }


def _puede_cursar(materia: models.Materia, estados: dict) -> bool:
    """
    Devuelve True si todos los prerequisitos de la materia están cumplidos.

    Reglas:
    - REGULARIZADA → la materia requerida debe ser REGULAR o PROMOCIONADA
    - APROBADA     → la materia requerida debe ser PROMOCIONADA
    """
    for prereq in materia.prerequisitos:
        estado_req = estados.get(prereq.materia_requerida_id, models.EstadoEnum.NO_CURSADA)
        if prereq.tipo == models.TipoPrerequisito.REGULARIZADA:
            if estado_req not in (models.EstadoEnum.REGULAR, models.EstadoEnum.PROMOCIONADA):
                return False
        elif prereq.tipo == models.TipoPrerequisito.APROBADA:
            if estado_req != models.EstadoEnum.PROMOCIONADA:
                return False
    return True


@router.get("/puedo-cursar", response_model=List[schemas.MateriaOut])
def materias_que_puedo_cursar(db: Session = Depends(get_db)):
    """
    Devuelve las materias que el alumno PUEDE CURSAR ahora mismo:
    - Están en estado NO_CURSADA.
    - Todos sus prerequisitos están cumplidos.
    """
    materias = db.query(models.Materia).all()
    estados = _estados_map(db)

    return [
        m for m in materias
        if estados.get(m.id, models.EstadoEnum.NO_CURSADA) == models.EstadoEnum.NO_CURSADA
        and _puede_cursar(m, estados)
    ]


@router.get("/puedo-rendir", response_model=List[schemas.MateriaOut])
def materias_que_puedo_rendir(db: Session = Depends(get_db)):
    """
    Devuelve las materias en estado REGULAR (cursadas, pendientes de final).
    """
    estados_db = db.query(models.EstadoMateria).filter(
        models.EstadoMateria.estado == models.EstadoEnum.REGULAR
    ).all()
    ids_regulares = {e.materia_id for e in estados_db}
    return db.query(models.Materia).filter(models.Materia.id.in_(ids_regulares)).all()


@router.get("/plan-completo", response_model=List[schemas.MateriaConEstado])
def plan_completo(db: Session = Depends(get_db)):
    """
    Devuelve el plan completo: cada materia con su estado actual,
    si puede cursarse/rendirse, y sus prerequisitos.
    """
    materias = db.query(models.Materia).order_by(
        models.Materia.anio, models.Materia.cuatrimestre, models.Materia.nombre
    ).all()
    estados = _estados_map(db)

    resultado = []
    for m in materias:
        estado_actual = estados.get(m.id, models.EstadoEnum.NO_CURSADA)
        if estado_actual == models.EstadoEnum.PROMOCIONADA:
            puede = False
        elif estado_actual == models.EstadoEnum.REGULAR:
            # Ya cursó, puede rendir el final
            puede = True
        else:
            # NO_CURSADA: puede cursar si cumple prerequisitos
            puede = _puede_cursar(m, estados)

        resultado.append(
            schemas.MateriaConEstado(
                materia=schemas.MateriaOut.model_validate(m),
                estado=estado_actual,
                puede_cursar=puede,
                prerequisitos=[schemas.PrerequisitoOut.model_validate(p) for p in m.prerequisitos],
            )
        )

    return resultado

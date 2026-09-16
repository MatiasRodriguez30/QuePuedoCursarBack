from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app import auth, models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/prerequisitos", tags=["Prerequisitos"])


def _generaria_ciclo(db: Session, materia_id: int, materia_requerida_id: int) -> bool:
    """True si agregar "materia_id requiere materia_requerida_id" cerraría un
    ciclo (A->B->C->A), dejando esa cadena imposible de cumplir jamás.
    BFS desde materia_requerida_id seguiendo la cadena de "requiere": si se
    llega de vuelta a materia_id, hay ciclo."""
    visitados = {materia_requerida_id}
    cola = [materia_requerida_id]
    while cola:
        actual = cola.pop()
        if actual == materia_id:
            return True
        siguientes = (
            db.query(models.Prerequisito.materia_requerida_id)
            .filter(models.Prerequisito.materia_id == actual)
            .all()
        )
        for (siguiente,) in siguientes:
            if siguiente not in visitados:
                visitados.add(siguiente)
                cola.append(siguiente)
    return False


@router.get("", response_model=List[schemas.PrerequisitoOut])
def listar_prerequisitos(
    carrera_id: int = Query(..., description="Sólo se listan los prerequisitos de materias de esta carrera"),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Lista los prerequisitos de las materias de UNA carrera."""
    return (
        db.query(models.Prerequisito)
        .join(models.Materia, models.Prerequisito.materia_id == models.Materia.id)
        .filter(models.Materia.carrera_id == carrera_id)
        .all()
    )


@router.post("", response_model=schemas.PrerequisitoOut, status_code=201)
async def crear_prerequisito(
    prereq: schemas.PrerequisitoCreate,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Agrega un prerequisito a una materia (sólo ADMIN).

    - tipo REGULARIZADA: la materia requerida debe estar en estado REGULAR o PROMOCIONADA.
    - tipo APROBADA: la materia requerida debe estar en estado PROMOCIONADA.
    """
    if prereq.materia_id == prereq.materia_requerida_id:
        raise HTTPException(status_code=400, detail="Una materia no puede ser prerequisito de sí misma")

    materia = db.query(models.Materia).filter(models.Materia.id == prereq.materia_id).first()
    if not materia:
        raise HTTPException(status_code=404, detail=f"Materia con id {prereq.materia_id} no encontrada")

    requerida = db.query(models.Materia).filter(models.Materia.id == prereq.materia_requerida_id).first()
    if not requerida:
        raise HTTPException(status_code=404, detail=f"Materia requerida con id {prereq.materia_requerida_id} no encontrada")

    if materia.carrera_id != requerida.carrera_id:
        raise HTTPException(status_code=400, detail="Ambas materias deben pertenecer a la misma carrera")

    existente = db.query(models.Prerequisito).filter(
        models.Prerequisito.materia_id == prereq.materia_id,
        models.Prerequisito.materia_requerida_id == prereq.materia_requerida_id,
    ).first()
    if existente:
        raise HTTPException(status_code=400, detail="Este prerequisito ya existe")

    if _generaria_ciclo(db, prereq.materia_id, prereq.materia_requerida_id):
        raise HTTPException(status_code=400, detail="No se puede agregar: genera una correlatividad circular")

    db_prereq = models.Prerequisito(**prereq.dict())
    db.add(db_prereq)
    db.commit()
    db.refresh(db_prereq)

    await manager.broadcast("prerequisito_creado", schemas.PrerequisitoOut.from_orm(db_prereq).dict())
    return db_prereq


@router.delete("/{prereq_id}", status_code=204)
async def eliminar_prerequisito(
    prereq_id: int,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Elimina un prerequisito por su ID (sólo ADMIN)."""
    prereq = db.query(models.Prerequisito).filter(models.Prerequisito.id == prereq_id).first()
    if not prereq:
        raise HTTPException(status_code=404, detail="Prerequisito no encontrado")

    db.delete(prereq)
    db.commit()

    await manager.broadcast("prerequisito_eliminado", {"id": prereq_id})

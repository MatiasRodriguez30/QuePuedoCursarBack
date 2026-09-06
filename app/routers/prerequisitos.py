from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/prerequisitos", tags=["Prerequisitos"])


@router.get("", response_model=List[schemas.PrerequisitoOut])
def listar_prerequisitos(db: Session = Depends(get_db)):
    """Lista todos los prerequisitos definidos."""
    return db.query(models.Prerequisito).all()


@router.post("", response_model=schemas.PrerequisitoOut, status_code=201)
async def crear_prerequisito(prereq: schemas.PrerequisitoCreate, db: Session = Depends(get_db)):
    """Agrega un prerequisito a una materia.
    
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

    existente = db.query(models.Prerequisito).filter(
        models.Prerequisito.materia_id == prereq.materia_id,
        models.Prerequisito.materia_requerida_id == prereq.materia_requerida_id,
    ).first()
    if existente:
        raise HTTPException(status_code=400, detail="Este prerequisito ya existe")

    db_prereq = models.Prerequisito(**prereq.dict())
    db.add(db_prereq)
    db.commit()
    db.refresh(db_prereq)

    await manager.broadcast("prerequisito_creado", schemas.PrerequisitoOut.from_orm(db_prereq).dict())
    return db_prereq


@router.delete("/{prereq_id}", status_code=204)
async def eliminar_prerequisito(prereq_id: int, db: Session = Depends(get_db)):
    """Elimina un prerequisito por su ID."""
    prereq = db.query(models.Prerequisito).filter(models.Prerequisito.id == prereq_id).first()
    if not prereq:
        raise HTTPException(status_code=404, detail="Prerequisito no encontrado")

    db.delete(prereq)
    db.commit()

    await manager.broadcast("prerequisito_eliminado", {"id": prereq_id})

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/materias", tags=["Materias"])


@router.get("", response_model=List[schemas.MateriaOut])
def listar_materias(db: Session = Depends(get_db)):
    """Devuelve todas las materias del plan de estudios."""
    return db.query(models.Materia).order_by(models.Materia.anio, models.Materia.cuatrimestre, models.Materia.nombre).all()


@router.post("", response_model=schemas.MateriaOut, status_code=201)
async def crear_materia(materia: schemas.MateriaCreate, db: Session = Depends(get_db)):
    """Crea una nueva materia. Le asigna automáticamente estado NO_CURSADA.

    El código es opcional: si no se especifica uno manualmente, se autogenera
    a partir del ID interno que le asigna la base de datos (por eso primero
    se necesita el flush, antes de fijar el código definitivo).
    """
    codigo_manual = (materia.codigo or "").strip() or None

    if codigo_manual:
        existente = db.query(models.Materia).filter(models.Materia.codigo == codigo_manual).first()
        if existente:
            raise HTTPException(status_code=400, detail=f"Ya existe una materia con código '{codigo_manual}'")

    datos = materia.dict(exclude={"codigo"})
    # Placeholder temporal mientras no conocemos el ID (la columna es NOT NULL + UNIQUE).
    db_materia = models.Materia(codigo=codigo_manual or "__pendiente__", **datos)
    db.add(db_materia)
    db.flush()  # para obtener el id antes del commit

    if not codigo_manual:
        db_materia.codigo = str(db_materia.id)

    estado = models.EstadoMateria(materia_id=db_materia.id, estado=models.EstadoEnum.NO_CURSADA)
    db.add(estado)
    db.commit()
    db.refresh(db_materia)

    await manager.broadcast("materia_creada", schemas.MateriaOut.from_orm(db_materia).dict())
    return db_materia


@router.get("/{materia_id}", response_model=schemas.MateriaOut)
def obtener_materia(materia_id: int, db: Session = Depends(get_db)):
    """Obtiene una materia por ID."""
    materia = db.query(models.Materia).filter(models.Materia.id == materia_id).first()
    if not materia:
        raise HTTPException(status_code=404, detail="Materia no encontrada")
    return materia


@router.put("/{materia_id}", response_model=schemas.MateriaOut)
async def actualizar_materia(materia_id: int, datos: schemas.MateriaUpdate, db: Session = Depends(get_db)):
    """Actualiza los datos de una materia.

    Si se manda `codigo` vacío, se regenera a partir del ID interno (igual que en creación).
    """
    materia = db.query(models.Materia).filter(models.Materia.id == materia_id).first()
    if not materia:
        raise HTTPException(status_code=404, detail="Materia no encontrada")

    campos = datos.dict(exclude_unset=True)
    if "codigo" in campos:
        nuevo_codigo = (campos["codigo"] or "").strip() or str(materia.id)
        if nuevo_codigo != materia.codigo:
            existente = (
                db.query(models.Materia)
                .filter(models.Materia.codigo == nuevo_codigo, models.Materia.id != materia_id)
                .first()
            )
            if existente:
                raise HTTPException(status_code=400, detail=f"Ya existe una materia con código '{nuevo_codigo}'")
        campos["codigo"] = nuevo_codigo

    for campo, valor in campos.items():
        setattr(materia, campo, valor)

    db.commit()
    db.refresh(materia)

    await manager.broadcast("materia_actualizada", schemas.MateriaOut.from_orm(materia).dict())
    return materia


@router.delete("/{materia_id}", status_code=204)
async def eliminar_materia(materia_id: int, db: Session = Depends(get_db)):
    """Elimina una materia y todos sus prerequisitos asociados."""
    materia = db.query(models.Materia).filter(models.Materia.id == materia_id).first()
    if not materia:
        raise HTTPException(status_code=404, detail="Materia no encontrada")

    db.delete(materia)
    db.commit()

    await manager.broadcast("materia_eliminada", {"id": materia_id})


@router.get("/{materia_id}/prerequisitos", response_model=List[schemas.PrerequisitoOut])
def prerequisitos_de_materia(materia_id: int, db: Session = Depends(get_db)):
    """Lista los prerequisitos de una materia específica."""
    materia = db.query(models.Materia).filter(models.Materia.id == materia_id).first()
    if not materia:
        raise HTTPException(status_code=404, detail="Materia no encontrada")
    return materia.prerequisitos

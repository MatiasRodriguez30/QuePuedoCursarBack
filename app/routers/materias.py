import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app import auth, models, schemas
from app.ws_manager import manager

router = APIRouter(prefix="/materias", tags=["Materias"])


@router.get("", response_model=List[schemas.MateriaOut])
def listar_materias(
    carrera_id: int = Query(..., description="Sólo se listan las materias de esta carrera"),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Devuelve las materias del plan de estudios de UNA carrera (cualquier
    usuario logueado puede ver cualquier carrera, no sólo la que trackea)."""
    return (
        db.query(models.Materia)
        .filter(models.Materia.carrera_id == carrera_id)
        .order_by(models.Materia.anio, models.Materia.cuatrimestre, models.Materia.nombre)
        .all()
    )


@router.post("", response_model=schemas.MateriaOut, status_code=201)
async def crear_materia(
    materia: schemas.MateriaCreate,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Crea una nueva materia (sólo ADMIN).

    El código es opcional: si no se especifica uno manualmente, se autogenera
    a partir del ID interno que le asigna la base de datos (por eso primero
    se necesita el flush, antes de fijar el código definitivo). El estado de
    esta materia para cada usuario se crea recién cuando cada uno la marca
    por primera vez (ver PUT /estados/{materia_id}).
    """
    carrera = db.query(models.Carrera).filter(models.Carrera.id == materia.carrera_id).first()
    if not carrera:
        raise HTTPException(status_code=404, detail="Carrera no encontrada")

    codigo_manual = (materia.codigo or "").strip() or None

    if codigo_manual:
        existente = db.query(models.Materia).filter(
            models.Materia.carrera_id == materia.carrera_id,
            models.Materia.codigo == codigo_manual,
        ).first()
        if existente:
            raise HTTPException(status_code=400, detail=f"Ya existe una materia con código '{codigo_manual}' en esta carrera")

    datos = materia.dict(exclude={"codigo"})
    # Placeholder temporal único mientras no conocemos el ID (la columna es
    # NOT NULL + UNIQUE por carrera): con un literal fijo, dos creaciones
    # concurrentes sin código en la misma carrera colisionarían con 500.
    placeholder = codigo_manual or f"__tmp_{secrets.token_hex(4)}__"
    db_materia = models.Materia(codigo=placeholder, **datos)
    db.add(db_materia)
    db.flush()  # para obtener el id antes del commit

    if not codigo_manual:
        db_materia.codigo = str(db_materia.id)

    db.commit()
    db.refresh(db_materia)

    await manager.broadcast("materia_creada", schemas.MateriaOut.from_orm(db_materia).dict())
    return db_materia


@router.get("/{materia_id}", response_model=schemas.MateriaOut)
def obtener_materia(
    materia_id: int,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Obtiene una materia por ID."""
    materia = db.query(models.Materia).filter(models.Materia.id == materia_id).first()
    if not materia:
        raise HTTPException(status_code=404, detail="Materia no encontrada")
    return materia


@router.put("/{materia_id}", response_model=schemas.MateriaOut)
async def actualizar_materia(
    materia_id: int,
    datos: schemas.MateriaUpdate,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
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
                .filter(
                    models.Materia.carrera_id == materia.carrera_id,
                    models.Materia.codigo == nuevo_codigo,
                    models.Materia.id != materia_id,
                )
                .first()
            )
            if existente:
                raise HTTPException(status_code=400, detail=f"Ya existe una materia con código '{nuevo_codigo}' en esta carrera")
        campos["codigo"] = nuevo_codigo

    for campo, valor in campos.items():
        setattr(materia, campo, valor)

    db.commit()
    db.refresh(materia)

    await manager.broadcast("materia_actualizada", schemas.MateriaOut.from_orm(materia).dict())
    return materia


@router.delete("/{materia_id}", status_code=204)
async def eliminar_materia(
    materia_id: int,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Elimina una materia y todos sus prerequisitos asociados (sólo ADMIN)."""
    materia = db.query(models.Materia).filter(models.Materia.id == materia_id).first()
    if not materia:
        raise HTTPException(status_code=404, detail="Materia no encontrada")

    db.delete(materia)
    db.commit()

    await manager.broadcast("materia_eliminada", {"id": materia_id})


@router.get("/{materia_id}/prerequisitos", response_model=List[schemas.PrerequisitoOut])
def prerequisitos_de_materia(
    materia_id: int,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(auth.get_current_user),
):
    """Lista los prerequisitos de una materia específica."""
    materia = db.query(models.Materia).filter(models.Materia.id == materia_id).first()
    if not materia:
        raise HTTPException(status_code=404, detail="Materia no encontrada")
    return materia.prerequisitos

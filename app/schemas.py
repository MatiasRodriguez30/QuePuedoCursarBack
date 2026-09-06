from __future__ import annotations
from typing import Optional, List
from pydantic import BaseModel, model_validator
from app.models import TipoPrerequisito, EstadoEnum


# ─────────────────────────────────────────────
# Materia
# ─────────────────────────────────────────────

class MateriaBase(BaseModel):
    codigo: str
    nombre: str
    descripcion: Optional[str] = None
    anio: Optional[int] = None
    cuatrimestre: Optional[int] = None
    horas_semanales: Optional[int] = None
    es_basica_compartida: bool = False


class MateriaCreate(MateriaBase):
    # Si se omite (o se manda vacío), el backend lo autogenera a partir del ID interno.
    codigo: Optional[str] = None


class MateriaUpdate(BaseModel):
    codigo: Optional[str] = None
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    anio: Optional[int] = None
    cuatrimestre: Optional[int] = None
    horas_semanales: Optional[int] = None
    es_basica_compartida: Optional[bool] = None


class MateriaOut(MateriaBase):
    id: int
    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# Prerequisito
# ─────────────────────────────────────────────

class PrerequisitoCreate(BaseModel):
    materia_id: int
    materia_requerida_id: int
    tipo: TipoPrerequisito


class PrerequisitoOut(BaseModel):
    id: int
    materia_id: int
    materia_requerida_id: int
    tipo: TipoPrerequisito
    materia_requerida: MateriaOut
    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# Estado
# ─────────────────────────────────────────────

class EstadoMateriaUpdate(BaseModel):
    estado: EstadoEnum


class EstadoMateriaOut(BaseModel):
    materia_id: int
    estado: EstadoEnum
    materia: MateriaOut
    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# Consultas
# ─────────────────────────────────────────────

class MateriaConEstado(BaseModel):
    materia: MateriaOut
    estado: EstadoEnum
    puede_cursar: bool
    prerequisitos: List[PrerequisitoOut]
    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# Configuración global (año / cuatrimestre actual del alumno)
# ─────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    anio_actual: Optional[int] = None
    cuatrimestre_actual: Optional[int] = None


class ConfigOut(BaseModel):
    anio_actual: Optional[int] = None
    cuatrimestre_actual: Optional[int] = None
    model_config = {"from_attributes": True}

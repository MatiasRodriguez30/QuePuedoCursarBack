from __future__ import annotations
from datetime import date, datetime, time
from typing import Optional, List
from pydantic import BaseModel
from app.models import TipoPrerequisito, EstadoEnum, RolEnum, OrigenEvento


# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UsuarioOut(BaseModel):
    id: int
    email: str
    rol: RolEnum

    class Config:
        orm_mode = True


class UsuarioAdminOut(BaseModel):
    """Igual que UsuarioOut, más datos que sólo le interesan al panel de
    administración (ver GET /usuarios, admin-only)."""
    id: int
    email: str
    rol: RolEnum
    creado_en: datetime

    class Config:
        orm_mode = True


class UsuarioRolUpdate(BaseModel):
    rol: RolEnum


class TokenOut(BaseModel):
    token: str
    usuario: UsuarioOut


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


# ─────────────────────────────────────────────
# Carrera
# ─────────────────────────────────────────────

class CarreraCreate(BaseModel):
    nombre: str
    plan_nombre: Optional[str] = None
    horas_excepcion_ultimo_anio: Optional[int] = None


class CarreraUpdate(BaseModel):
    nombre: Optional[str] = None
    plan_nombre: Optional[str] = None
    horas_excepcion_ultimo_anio: Optional[int] = None


class CarreraOut(BaseModel):
    id: int
    nombre: str
    plan_nombre: Optional[str] = None
    horas_excepcion_ultimo_anio: Optional[int] = None

    class Config:
        orm_mode = True


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
    carrera_id: int
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
    carrera_id: Optional[int] = None

    class Config:
        orm_mode = True


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

    class Config:
        orm_mode = True


# ─────────────────────────────────────────────
# Estado
# ─────────────────────────────────────────────

class EstadoMateriaUpdate(BaseModel):
    estado: EstadoEnum


class EstadoMateriaOut(BaseModel):
    materia_id: int
    usuario_id: int
    estado: EstadoEnum
    materia: MateriaOut

    class Config:
        orm_mode = True


# ─────────────────────────────────────────────
# Consultas
# ─────────────────────────────────────────────

class MateriaConEstado(BaseModel):
    materia: MateriaOut
    estado: EstadoEnum
    puede_cursar: bool
    prerequisitos: List[PrerequisitoOut]

    class Config:
        orm_mode = True


# ─────────────────────────────────────────────
# Configuración por carrera (año / cuatrimestre actual del alumno)
# ─────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    anio_actual: Optional[int] = None
    cuatrimestre_actual: Optional[int] = None


class ConfigOut(BaseModel):
    carrera_id: Optional[int] = None
    anio_actual: Optional[int] = None
    cuatrimestre_actual: Optional[int] = None

    class Config:
        orm_mode = True


# ─────────────────────────────────────────────
# Evento (agenda)
# ─────────────────────────────────────────────

class EventoCreate(BaseModel):
    titulo: str
    descripcion: Optional[str] = None
    ubicacion: Optional[str] = None
    fecha: date
    hora_inicio: Optional[time] = None
    hora_fin: Optional[time] = None


class EventoUpdate(BaseModel):
    titulo: Optional[str] = None
    descripcion: Optional[str] = None
    ubicacion: Optional[str] = None
    fecha: Optional[date] = None
    hora_inicio: Optional[time] = None
    hora_fin: Optional[time] = None


class EventoOut(BaseModel):
    id: int
    titulo: str
    descripcion: Optional[str] = None
    ubicacion: Optional[str] = None
    fecha: date
    hora_inicio: Optional[time] = None
    hora_fin: Optional[time] = None
    origen: OrigenEvento
    creado_por_id: Optional[int] = None

    class Config:
        orm_mode = True

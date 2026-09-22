"""Panel de base de datos para el administrador: sólo lectura.

Introspecta el esquema real desde app.models.Base.metadata (no hay que
mantener una lista de tablas a mano: cualquier tabla nueva aparece sola).
No permite editar ni borrar nada — es para mirar, no para operar.
"""
import enum
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, models
from app.database import get_db

router = APIRouter(prefix="/admin/db", tags=["Admin BD"])

# Columnas que nunca se mandan al frontend, aunque sea al administrador:
# password_hash y los tokens permitirían iniciar sesión como cualquiera.
COLUMNAS_SENSIBLES = {
    ("usuarios", "password_hash"),
    ("sesiones", "token"),
    ("password_reset_tokens", "token"),
}
VALOR_OCULTO = "••••••"

LIMITE_DEFAULT = 50
LIMITE_MAXIMO = 200


class ReferenciaOut(BaseModel):
    tabla: str
    columna: str


class ColumnaOut(BaseModel):
    nombre: str
    tipo: str
    nullable: bool
    primary_key: bool
    sensible: bool
    referencia: Optional[ReferenciaOut] = None


class TablaOut(BaseModel):
    nombre: str
    filas: int
    columnas: List[ColumnaOut]


class FilasOut(BaseModel):
    total: int
    offset: int
    limit: int
    columnas: List[str]
    filas: List[Dict[str, Any]]


def _columna_a_schema(tabla_nombre: str, columna) -> ColumnaOut:
    referencia = None
    if columna.foreign_keys:
        # Una columna puede referenciar más de una tabla en teoría; en este
        # esquema no pasa, así que alcanza con la primera.
        fk = next(iter(columna.foreign_keys))
        referencia = ReferenciaOut(tabla=fk.column.table.name, columna=fk.column.name)
    return ColumnaOut(
        nombre=columna.name,
        tipo=str(columna.type),
        nullable=bool(columna.nullable),
        primary_key=bool(columna.primary_key),
        sensible=(tabla_nombre, columna.name) in COLUMNAS_SENSIBLES,
        referencia=referencia,
    )


def _serializar_valor(valor: Any) -> Any:
    if isinstance(valor, enum.Enum):
        return valor.value
    if isinstance(valor, (datetime, date, time)):
        return valor.isoformat()
    if isinstance(valor, bytes):
        return None
    return valor


def _tabla_o_404(nombre: str):
    tabla = models.Base.metadata.tables.get(nombre)
    if tabla is None:
        raise HTTPException(status_code=404, detail="No existe una tabla \"{}\"".format(nombre))
    return tabla


@router.get("/tablas", response_model=List[TablaOut])
def listar_tablas(
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Todas las tablas del esquema, con su cantidad de filas y sus columnas
    (tipo, si es clave primaria y a qué tabla/columna referencia, si es FK).
    Las columnas marcadas `sensible` nunca traen su valor real en /filas."""
    salida = []
    for nombre, tabla in models.Base.metadata.tables.items():
        total = db.execute(select(func.count()).select_from(tabla)).scalar() or 0
        salida.append(TablaOut(
            nombre=nombre,
            filas=total,
            columnas=[_columna_a_schema(nombre, c) for c in tabla.columns],
        ))
    salida.sort(key=lambda t: t.nombre)
    return salida


@router.get("/tablas/{nombre}/filas", response_model=FilasOut)
def listar_filas(
    nombre: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=LIMITE_DEFAULT, ge=1, le=LIMITE_MAXIMO),
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(auth.require_admin),
):
    """Filas de una tabla, paginadas y ordenadas por su clave primaria (si
    tiene). Las columnas sensibles vienen ocultas, no ausentes: así se ve
    que existen sin exponer su valor."""
    tabla = _tabla_o_404(nombre)
    columnas_nombres = [c.name for c in tabla.columns]
    columnas_sensibles = {c.name for c in tabla.columns if (nombre, c.name) in COLUMNAS_SENSIBLES}

    total = db.execute(select(func.count()).select_from(tabla)).scalar() or 0

    consulta = select(tabla)
    if tabla.primary_key.columns:
        consulta = consulta.order_by(*tabla.primary_key.columns)
    consulta = consulta.offset(offset).limit(limit)

    filas = []
    for fila in db.execute(consulta).mappings().all():
        registro = {}
        for col_nombre in columnas_nombres:
            if col_nombre in columnas_sensibles:
                registro[col_nombre] = VALOR_OCULTO
            else:
                registro[col_nombre] = _serializar_valor(fila[col_nombre])
        filas.append(registro)

    return FilasOut(total=total, offset=offset, limit=limit, columnas=columnas_nombres, filas=filas)

import enum
from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy import Enum as SAEnum
from app.database import Base


class TipoPrerequisito(str, enum.Enum):
    REGULARIZADA = "REGULARIZADA"
    APROBADA = "APROBADA"


class EstadoEnum(str, enum.Enum):
    PROMOCIONADA = "PROMOCIONADA"
    REGULAR = "REGULAR"
    CURSANDO = "CURSANDO"
    NO_CURSADA = "NO_CURSADA"


class Materia(Base):
    __tablename__ = "materias"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String, unique=True, index=True, nullable=False)
    nombre = Column(String, nullable=False)
    descripcion = Column(String, nullable=True)
    anio = Column(Integer, nullable=True)
    cuatrimestre = Column(Integer, nullable=True)
    # Carga horaria semanal (según el plan de estudios). Se usa para calcular
    # excepciones de correlatividad basadas en horas (ej. Ordenanza 1872).
    horas_semanales = Column(Integer, nullable=True)
    # Marca materias básicas de 1º/2º año que se dictan en una comisión general
    # compartida entre varias especialidades (Sistemas, Civil, Electromecánica, etc.),
    # por lo que se pueden cursar en un horario/comisión distinto al propio de Sistemas.
    es_basica_compartida = Column(Boolean, nullable=False, default=False)

    # Prerequisitos que TIENE esta materia (lo que necesita para cursarse)
    prerequisitos = relationship(
        "Prerequisito",
        foreign_keys="Prerequisito.materia_id",
        back_populates="materia",
        cascade="all, delete-orphan",
    )
    # Relaciones donde esta materia ES requerida por otras
    requerida_por = relationship(
        "Prerequisito",
        foreign_keys="Prerequisito.materia_requerida_id",
        back_populates="materia_requerida",
    )
    # Estado actual del alumno en esta materia
    estado = relationship(
        "EstadoMateria",
        back_populates="materia",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Prerequisito(Base):
    __tablename__ = "prerequisitos"

    id = Column(Integer, primary_key=True, index=True)
    materia_id = Column(Integer, ForeignKey("materias.id"), nullable=False)
    materia_requerida_id = Column(Integer, ForeignKey("materias.id"), nullable=False)
    tipo = Column(SAEnum(TipoPrerequisito), nullable=False)

    materia = relationship(
        "Materia", foreign_keys=[materia_id], back_populates="prerequisitos"
    )
    materia_requerida = relationship(
        "Materia", foreign_keys=[materia_requerida_id], back_populates="requerida_por"
    )

    __table_args__ = (
        UniqueConstraint("materia_id", "materia_requerida_id", name="uq_prereq"),
    )


class EstadoMateria(Base):
    __tablename__ = "estados_materia"

    id = Column(Integer, primary_key=True, index=True)
    materia_id = Column(Integer, ForeignKey("materias.id"), unique=True, nullable=False)
    estado = Column(
        SAEnum(EstadoEnum), nullable=False, default=EstadoEnum.NO_CURSADA
    )

    materia = relationship("Materia", back_populates="estado")


class ConfigApp(Base):
    """Fila única (id=1) con configuración global: en qué año/cuatrimestre
    calendario dice estar parado el alumno. Se usa para calcular próximas
    oportunidades de cursado y la ruta sugerida, en lugar de adivinar a
    partir de la fecha del dispositivo."""

    __tablename__ = "config_app"

    id = Column(Integer, primary_key=True, default=1)
    anio_actual = Column(Integer, nullable=True)
    cuatrimestre_actual = Column(Integer, nullable=True)  # 1 o 2

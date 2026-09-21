import enum
from datetime import datetime

from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, Boolean, DateTime, Date, Time, Text
from sqlalchemy.orm import relationship
from sqlalchemy import Enum as SAEnum
from app.database import Base


class TipoPrerequisito(str, enum.Enum):
    REGULARIZADA = "REGULARIZADA"
    APROBADA = "APROBADA"


class RolEnum(str, enum.Enum):
    ADMIN = "ADMIN"
    USER = "USER"


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    # Siempre normalizado a minúscula (ver app.auth.normalizar_email) para que
    # no importe cómo lo tipeen al loguearse.
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    rol = Column(SAEnum(RolEnum), nullable=False, default=RolEnum.USER)
    creado_en = Column(DateTime, default=datetime.utcnow)
    # Nombre que ven los demás miembros de su grupo. La columna en la base se
    # llama "apodo" (se agrega con app.migraciones); el atributo Python
    # `apodo` (abajo) siempre devuelve un valor: si la persona no eligió
    # uno, "Cobayo N", para no exponer nada del email.
    apodo_db = Column("apodo", String, nullable=True)

    estados = relationship("EstadoMateria", back_populates="usuario", cascade="all, delete-orphan")
    membresia = relationship("Membresia", back_populates="usuario", uselist=False, cascade="all, delete-orphan")

    @property
    def apodo(self):
        return self.apodo_db or "Cobayo {}".format(self.id)


class Grupo(Base):
    """Grupo de amigos al que se entra con un código de invitación."""

    __tablename__ = "grupos"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    codigo = Column(String, unique=True, nullable=False, index=True)
    creado_en = Column(DateTime, default=datetime.utcnow)

    miembros = relationship("Membresia", back_populates="grupo", cascade="all, delete-orphan")


class Membresia(Base):
    """Un usuario pertenece a lo sumo a UN grupo (usuario_id único)."""

    __tablename__ = "membresias"

    id = Column(Integer, primary_key=True, index=True)
    grupo_id = Column(Integer, ForeignKey("grupos.id", ondelete="CASCADE"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True)
    # Si comparte su progreso con el grupo (logros, y a futuro "quién cursa qué").
    comparte = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime, default=datetime.utcnow)

    grupo = relationship("Grupo", back_populates="miembros")
    usuario = relationship("Usuario", back_populates="membresia")


class Sesion(Base):
    """Token de sesión simple (no JWT): opaco, guardado en la base, con
    vencimiento. Evita depender de librerías con extensiones nativas
    (bcrypt/pyjwt con binarios) que no compilan fácil en la tablet ARM."""

    __tablename__ = "sesiones"

    token = Column(String, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    creado_en = Column(DateTime, default=datetime.utcnow)
    expira_en = Column(DateTime, nullable=False)

    usuario = relationship("Usuario")


class PasswordResetToken(Base):
    """Token de un solo uso para el flujo de 'olvidé mi contraseña', enviado
    por email vía Resend. Corta duración (ver ttl en app/auth.py)."""

    __tablename__ = "password_reset_tokens"

    token = Column(String, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    creado_en = Column(DateTime, default=datetime.utcnow)
    expira_en = Column(DateTime, nullable=False)
    usado = Column(Boolean, nullable=False, default=False)


class EstadoEnum(str, enum.Enum):
    PROMOCIONADA = "PROMOCIONADA"
    REGULAR = "REGULAR"
    CURSANDO = "CURSANDO"
    NO_CURSADA = "NO_CURSADA"


class Carrera(Base):
    """Una carrera universitaria (ej. "Ingeniería en Sistemas", Plan 2023).
    Cada una tiene su propio plan de materias y correlatividades, totalmente
    independiente de las demás. Un usuario puede tener progreso guardado en
    varias carreras a la vez (su EstadoMateria queda ligado a la materia, que
    a su vez pertenece a una sola carrera)."""

    __tablename__ = "carreras"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, nullable=False)
    plan_nombre = Column(String, nullable=True)  # ej. "Plan 2023", sólo descriptivo
    # Límite de horas semanales del último año para la excepción de "Adelanto
    # de Nivel" (Ordenanza 1872, ver businessLogic.checkExcepcionMachete en el
    # frontend). Null = esta carrera no tiene esa excepción disponible.
    horas_excepcion_ultimo_anio = Column(Integer, nullable=True)

    materias = relationship("Materia", back_populates="carrera", cascade="all, delete-orphan")
    config = relationship("ConfigApp", back_populates="carrera", uselist=False, cascade="all, delete-orphan")


class Materia(Base):
    __tablename__ = "materias"

    id = Column(Integer, primary_key=True, index=True)
    # Nullable a nivel DB sólo para permitir la migración de filas cargadas
    # antes de que existiera el sistema de carreras (ver scripts/migrate_v3_carreras.py).
    # La aplicación siempre lo completa al crear una materia nueva.
    carrera_id = Column(Integer, ForeignKey("carreras.id"), nullable=True)
    # El código ya no es único globalmente (dos carreras distintas pueden
    # reusar el mismo código), sólo dentro de su propia carrera.
    codigo = Column(String, index=True, nullable=False)
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

    carrera = relationship("Carrera", back_populates="materias")
    # Prerequisitos que TIENE esta materia (lo que necesita para cursarse)
    prerequisitos = relationship(
        "Prerequisito",
        foreign_keys="Prerequisito.materia_id",
        back_populates="materia",
        cascade="all, delete-orphan",
    )
    # Relaciones donde esta materia ES requerida por otras
    # cascade: si se borra esta materia, también se borran los prerequisitos
    # de OTRAS materias que la exigían a ella (si no, quedan filas huérfanas
    # con materia_requerida_id apuntando a un id inexistente y la próxima
    # serialización de /prerequisitos revienta con 500).
    requerida_por = relationship(
        "Prerequisito",
        foreign_keys="Prerequisito.materia_requerida_id",
        back_populates="materia_requerida",
        cascade="all, delete-orphan",
    )
    # Estados de esta materia: uno por cada usuario que la haya marcado.
    estados = relationship(
        "EstadoMateria",
        back_populates="materia",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("carrera_id", "codigo", name="uq_materia_carrera_codigo"),
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
    materia_id = Column(Integer, ForeignKey("materias.id"), nullable=False)
    # Nullable a nivel DB solo para permitir la migración de filas "huérfanas"
    # (progreso cargado antes de que existiera el sistema de usuarios). La
    # aplicación siempre lo completa al crear una fila nueva.
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    estado = Column(
        SAEnum(EstadoEnum), nullable=False, default=EstadoEnum.NO_CURSADA
    )

    materia = relationship("Materia", back_populates="estados")
    usuario = relationship("Usuario", back_populates="estados")

    __table_args__ = (
        # Cada usuario tiene a lo sumo un estado por materia (progreso propio).
        UniqueConstraint("usuario_id", "materia_id", name="uq_estado_usuario_materia"),
    )


class ConfigApp(Base):
    """Una fila por carrera con en qué año/cuatrimestre calendario dice estar
    parado el alumno DE ESA CARRERA. Se usa para calcular próximas
    oportunidades de cursado y la ruta sugerida, en lugar de adivinar a
    partir de la fecha del dispositivo."""

    __tablename__ = "config_app"

    id = Column(Integer, primary_key=True)
    # Nullable a nivel DB sólo por la migración desde la fila única anterior
    # (ver scripts/migrate_v3_carreras.py); la aplicación siempre la completa.
    carrera_id = Column(Integer, ForeignKey("carreras.id"), unique=True, nullable=True)
    anio_actual = Column(Integer, nullable=True)
    cuatrimestre_actual = Column(Integer, nullable=True)  # 1 o 2

    carrera = relationship("Carrera", back_populates="config")


class OrigenEvento(str, enum.Enum):
    MANUAL = "MANUAL"
    IMPORTADO = "IMPORTADO"  # cargado desde el calendario .ics de UTN


class Evento(Base):
    """Agenda compartida entre todos los usuarios (institucional + personal).
    No hay progreso por-usuario acá como en EstadoMateria: cualquier usuario
    logueado ve todos los eventos, sólo un ADMIN puede crear/editar/borrar."""

    __tablename__ = "eventos"

    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String, nullable=False)
    descripcion = Column(Text, nullable=True)
    ubicacion = Column(String, nullable=True)
    fecha = Column(Date, nullable=False, index=True)
    hora_inicio = Column(Time, nullable=True)  # null = evento de todo el día
    hora_fin = Column(Time, nullable=True)
    origen = Column(SAEnum(OrigenEvento), nullable=False, default=OrigenEvento.MANUAL)
    # UID del VEVENT de origen, sólo para eventos IMPORTADOs: evita duplicar
    # si el script de importación se corre más de una vez sobre el mismo .ics.
    uid_ics = Column(String, unique=True, nullable=True)
    creado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow)

    creado_por = relationship("Usuario")

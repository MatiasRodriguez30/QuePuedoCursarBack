"""
test_scheduler_recordatorios.py — Contenido del mail diario de recordatorios.

Reproduce el mismo bug que test_eventos.py pero para el mail: un evento
personal no debe aparecer en el recordatorio de otro usuario. Los
institucionales sí van en el mail de todos.
"""
from datetime import date, datetime

from sqlalchemy.orm import sessionmaker

from app import models, scheduler


def _usuario(db, email):
    u = models.Usuario(email=email, password_hash="x", rol=models.RolEnum.USER)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _evento(db, titulo, fecha, personal=False, creado_por_id=None, hora_inicio=None):
    e = models.Evento(titulo=titulo, fecha=fecha, personal=personal, creado_por_id=creado_por_id, hora_inicio=hora_inicio)
    db.add(e)
    db.commit()
    return e


def test_evento_personal_solo_llega_al_mail_de_su_dueno(db_session, monkeypatch):
    fabrica = sessionmaker(autocommit=False, autoflush=False, bind=db_session.bind)
    monkeypatch.setattr(scheduler, "SessionLocal", fabrica)
    monkeypatch.setattr(scheduler, "_ahora_argentina", lambda: datetime(2026, 9, 29))

    admin = _usuario(db_session, "admin@mail.com")
    otro = _usuario(db_session, "otro@mail.com")
    manana = date(2026, 9, 30)

    _evento(db_session, "Rendir oral", manana, personal=True, creado_por_id=admin.id)
    _evento(db_session, "6° Mesas de examen", manana, personal=False)

    mails = {}

    def enviar_falso(to, subject, html):
        mails[to] = html
        return True

    monkeypatch.setattr(scheduler, "enviar_email", enviar_falso)

    scheduler.enviar_recordatorios_del_dia_siguiente()

    assert "Rendir oral" in mails["admin@mail.com"]
    assert "6° Mesas de examen" in mails["admin@mail.com"]
    # Al otro usuario le llega lo institucional, pero NO el evento personal ajeno.
    assert "6° Mesas de examen" in mails["otro@mail.com"]
    assert "Rendir oral" not in mails["otro@mail.com"]


def test_usuario_sin_eventos_propios_no_recibe_mail(db_session, monkeypatch):
    fabrica = sessionmaker(autocommit=False, autoflush=False, bind=db_session.bind)
    monkeypatch.setattr(scheduler, "SessionLocal", fabrica)
    monkeypatch.setattr(scheduler, "_ahora_argentina", lambda: datetime(2026, 9, 29))

    dueno = _usuario(db_session, "dueno@mail.com")
    sin_eventos = _usuario(db_session, "sineventos@mail.com")
    manana = date(2026, 9, 30)
    _evento(db_session, "Parcial Inglés", manana, personal=True, creado_por_id=dueno.id)

    mails = {}
    monkeypatch.setattr(scheduler, "enviar_email", lambda to, subject, html: mails.setdefault(to, html) or True)

    scheduler.enviar_recordatorios_del_dia_siguiente()

    assert "dueno@mail.com" in mails
    assert "sineventos@mail.com" not in mails

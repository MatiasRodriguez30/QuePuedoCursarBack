"""
test_sistema_info.py — Datos del equipo (batería, temperatura, RAM, Wi-Fi,
discos), los comandos /pc /servicios /estado del bot y los avisos de corte de
luz. Se usan carpetas falsas de /sys y /proc (HOST_SYS / HOST_PROC): nada de
esto depende de la máquina donde corre.
"""
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app import config, models, sistema_info, telegram_bot

CHAT = "555"


def _escribir(ruta, texto):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(texto)


@pytest.fixture
def equipo(tmp_path, monkeypatch):
    """Un 'equipo' falso: laptop enchufada con batería al 79%, sin cargar."""
    sys_ = tmp_path / "sys"
    proc = tmp_path / "proc"
    ps = sys_ / "class" / "power_supply"
    _escribir(ps / "BAT0" / "type", "Battery\n")
    _escribir(ps / "BAT0" / "capacity", "79\n")
    _escribir(ps / "BAT0" / "status", "Not charging\n")
    _escribir(ps / "AC" / "type", "Mains\n")
    _escribir(ps / "AC" / "online", "1\n")
    _escribir(sys_ / "class" / "thermal" / "thermal_zone0" / "type", "acpitz\n")
    _escribir(sys_ / "class" / "thermal" / "thermal_zone0" / "temp", "40000\n")
    _escribir(sys_ / "class" / "thermal" / "thermal_zone1" / "type", "x86_pkg_temp\n")
    _escribir(sys_ / "class" / "thermal" / "thermal_zone1" / "temp", "52000\n")
    _escribir(proc / "meminfo", "MemTotal:       16384000 kB\nMemFree: 1 kB\nMemAvailable:   14336000 kB\n")
    _escribir(proc / "loadavg", "0.15 0.20 0.25 1/300 1234\n")
    _escribir(proc / "uptime", "93784.12 5000.00\n")
    _escribir(
        proc / "net" / "wireless",
        "Inter-| sta-|   Quality        |   Discarded packets\n"
        " face | tus | link level noise |  nwid\n"
        "wlp164s0: 0000   70.  -52.  -256        0\n",
    )
    monkeypatch.setenv("HOST_SYS", str(sys_))
    monkeypatch.setenv("HOST_PROC", str(proc))
    return sys_, proc


# ─── Lecturas ─────────────────────────────────────────────────────────────────

def test_bateria_conectada_pero_sin_cargar(equipo):
    b = sistema_info.bateria()
    assert b == {"porcentaje": 79, "estado": "Not charging", "enchufado": True}
    texto = sistema_info.describir_bateria(b)
    assert "79%" in texto and "sin cargar" in texto and "enchufado" in texto and "SIN" not in texto


def test_bateria_descargando_y_sin_enchufar(equipo):
    sys_, _ = equipo
    _escribir(sys_ / "class" / "power_supply" / "BAT0" / "status", "Discharging\n")
    _escribir(sys_ / "class" / "power_supply" / "AC" / "online", "0\n")
    texto = sistema_info.describir_bateria(sistema_info.bateria())
    assert "DESCARGANDO" in texto and "SIN enchufar" in texto


def test_equipo_sin_bateria(tmp_path, monkeypatch):
    monkeypatch.setenv("HOST_SYS", str(tmp_path / "nada"))
    assert sistema_info.bateria() is None


def test_temperatura_prefiere_el_sensor_de_cpu(equipo):
    assert sistema_info.temperatura_cpu() == 52.0


def test_memoria_carga_uptime_y_wifi(equipo):
    assert sistema_info.memoria() == (16000, 14000)
    assert sistema_info.carga() == (0.15, 0.20, 0.25)
    assert sistema_info.uptime_segundos() == pytest.approx(93784.12)
    assert sistema_info.wifi() == ("wlp164s0", -52)
    assert sistema_info.calidad_wifi(-52) == "buena"
    assert sistema_info.calidad_wifi(-65) == "aceptable"
    assert sistema_info.calidad_wifi(-80) == "débil"


def test_datos_que_faltan_no_rompen(tmp_path, monkeypatch):
    monkeypatch.setenv("HOST_SYS", str(tmp_path / "x"))
    monkeypatch.setenv("HOST_PROC", str(tmp_path / "y"))
    assert sistema_info.temperatura_cpu() is None
    assert sistema_info.memoria() is None
    assert sistema_info.carga() is None
    assert sistema_info.uptime_segundos() is None
    assert sistema_info.wifi() is None


def test_discos_no_repite_el_mismo_disco(tmp_path):
    lista = sistema_info.discos([("A", str(tmp_path)), ("B", str(tmp_path)), ("C", str(tmp_path / "no-existe"))])
    assert [n for n, _, _ in lista] == ["A"]


# ─── Avisos de corte de luz ───────────────────────────────────────────────────

def _lectura(estado, pct=80):
    return {"estado": estado, "porcentaje": pct, "enchufado": estado != "Discharging"}


def test_corte_de_luz_se_avisa_solo_al_confirmarse_y_una_vez():
    estado = {}
    assert telegram_bot._procesar_energia(estado, _lectura("Full")) == []           # estado inicial: no avisa
    assert telegram_bot._procesar_energia(estado, _lectura("Discharging")) == []    # 1.ª lectura: puede ser un parpadeo
    avisos = telegram_bot._procesar_energia(estado, _lectura("Discharging"))        # 2.ª seguida: se confirma
    assert len(avisos) == 1 and "Se cortó la luz" in avisos[0]
    assert telegram_bot._procesar_energia(estado, _lectura("Discharging")) == []    # no repite


def test_un_parpadeo_del_sensor_no_dispara_alarma():
    estado = {}
    telegram_bot._procesar_energia(estado, _lectura("Full"))
    assert telegram_bot._procesar_energia(estado, _lectura("Discharging")) == []
    assert telegram_bot._procesar_energia(estado, _lectura("Full")) == []
    assert telegram_bot._procesar_energia(estado, _lectura("Discharging")) == []    # vuelve a contar de cero


def test_vuelve_la_corriente_y_bateria_baja():
    estado = {}
    telegram_bot._procesar_energia(estado, _lectura("Discharging", 50))
    assert telegram_bot._procesar_energia(estado, _lectura("Discharging", 19)) == [
        "Batería baja: 19%. Si no vuelve la corriente, el servidor se apaga pronto."
    ]
    assert telegram_bot._procesar_energia(estado, _lectura("Discharging", 18)) == []  # una sola vez
    telegram_bot._procesar_energia(estado, _lectura("Charging", 18))
    avisos = telegram_bot._procesar_energia(estado, _lectura("Charging", 19))
    assert len(avisos) == 1 and "Volvió la corriente" in avisos[0]


def test_sin_lectura_de_bateria_no_hace_nada():
    assert telegram_bot._procesar_energia({}, None) == []


# ─── Comandos /pc /servicios /estado ──────────────────────────────────────────

@pytest.fixture
def bot_listo(db_session, equipo, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:TOKEN-DE-PRUEBA")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.setattr(config, "TELEGRAM_USUARIO_EMAIL", "dueno@mail.com")
    fabrica = sessionmaker(autocommit=False, autoflush=False, bind=db_session.bind)
    monkeypatch.setattr(telegram_bot, "SessionLocal", fabrica)
    monkeypatch.setattr(telegram_bot, "_ahora_argentina", lambda: datetime(2026, 10, 6, 10, 0))
    monkeypatch.setattr(telegram_bot, "_probar_publico", lambda: "OK (HTTP 200, 120 ms)")
    db_session.add(models.Usuario(email="dueno@mail.com", password_hash="x", rol=models.RolEnum.USER))
    db_session.commit()
    return db_session


def test_pc_informa_bateria_temperatura_ram_y_wifi(bot_listo):
    texto = telegram_bot.responder("/pc", CHAT)
    assert "Batería: 79%" in texto and "sin cargar" in texto
    assert "Temperatura CPU: 52 °C" in texto
    assert "RAM: 14000 MB libres de 16000 MB" in texto
    assert "Wi-Fi (wlp164s0): -52 dBm" in texto
    assert "Encendido hace: 1d 2h" in texto


def test_servicios_informa_api_base_dominio_y_recordatorio(bot_listo):
    texto = telegram_bot.responder("/servicios", CHAT)
    assert "Servicios de Qué Puedo Cursar" in texto
    assert "API: activa hace" in texto
    assert "Base de datos: OK (1 usuarios" in texto
    assert "Dominio público: OK (HTTP 200, 120 ms)" in texto
    assert "Recordatorio de las 21:00: programado" in texto
    assert "Conexiones en vivo:" in texto


def test_servicios_avisa_si_el_dominio_publico_no_responde(bot_listo, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_probar_publico", lambda: "ERROR HTTP 530 (530 = túnel caído)")
    assert "ERROR HTTP 530" in telegram_bot.responder("/servicios", CHAT)


def test_estado_junta_servicios_y_equipo_sin_secretos(bot_listo):
    texto = telegram_bot.responder("/estado", CHAT)
    assert "Servicios de Qué Puedo Cursar" in texto and "El equipo" in texto
    assert "TOKEN-DE-PRUEBA" not in texto


def test_en_docker_sin_pid_no_dice_que_el_tunel_esta_caido(bot_listo):
    # Sin .cloudflared.pid (modo Docker) no debe aparecer una falsa alarma de túnel.
    assert "Túnel (cloudflared): NO responde" not in telegram_bot.responder("/servicios", CHAT)


def test_comandos_nuevos_solo_para_el_chat_del_dueno(bot_listo):
    for comando in ("/pc", "/servicios", "/estado"):
        assert telegram_bot.responder(comando, "999") is None


# ─── Consumo eléctrico ────────────────────────────────────────────────────────

def _bat(equipo):
    sys_, _ = equipo
    return sys_ / "class" / "power_supply" / "BAT0"


def test_consumo_total_con_corriente_y_voltaje_solo_si_descarga(equipo):
    bat = _bat(equipo)
    _escribir(bat / "status", "Discharging\n")
    _escribir(bat / "current_now", "600000\n")     # 0,6 A
    _escribir(bat / "voltage_now", "11500000\n")   # 11,5 V
    assert sistema_info.consumo_bateria() == pytest.approx(6.9)


def test_consumo_prefiere_power_now_si_la_bateria_lo_informa(equipo):
    bat = _bat(equipo)
    _escribir(bat / "status", "Discharging\n")
    _escribir(bat / "power_now", "7250000\n")      # µW
    _escribir(bat / "current_now", "1\n")
    _escribir(bat / "voltage_now", "1\n")
    assert sistema_info.consumo_bateria() == pytest.approx(7.25)


def test_enchufado_no_hay_consumo_total(equipo):
    bat = _bat(equipo)
    _escribir(bat / "current_now", "1000\n")
    _escribir(bat / "voltage_now", "11587000\n")
    assert sistema_info.consumo_bateria() is None   # status "Not charging": ese flujo no es el consumo


@pytest.fixture
def rapl(equipo, monkeypatch):
    """Carpeta del sensor RAPL falso (nombre sin ":" para poder crearla en Windows)."""
    monkeypatch.setattr(sistema_info, "RAPL_DIR", "intel-rapl-0")
    sys_, _ = equipo
    return sys_ / "class" / "powercap" / "intel-rapl-0"


def test_potencia_de_cpu_con_rapl(rapl, monkeypatch):
    _escribir(rapl / "energy_uj", "1000000\n")
    monkeypatch.setattr(sistema_info, "_dormir", lambda s: _escribir(rapl / "energy_uj", "4000000\n"))
    assert sistema_info.potencia_cpu(esperar=1.0) == pytest.approx(3.0)   # 3 J en 1 s = 3 W


def test_potencia_de_cpu_cuando_el_contador_da_la_vuelta(rapl, monkeypatch):
    _escribir(rapl / "energy_uj", "9500000\n")
    _escribir(rapl / "max_energy_range_uj", "10000000\n")
    monkeypatch.setattr(sistema_info, "_dormir", lambda s: _escribir(rapl / "energy_uj", "1500000\n"))
    assert sistema_info.potencia_cpu(esperar=2.0) == pytest.approx(1.0)   # (1,5M + 10M - 9,5M) µJ / 2 s


def test_sin_sensor_rapl_no_hay_potencia_de_cpu(equipo):
    assert sistema_info.potencia_cpu() is None


def test_texto_enchufado_explica_por_que_no_mide(equipo):
    lineas = sistema_info.describir_consumo(esperar=0.0)
    assert any("no se puede medir enchufado" in l for l in lineas)


def test_texto_descargando_da_vatios_y_kwh_por_mes(equipo):
    bat = _bat(equipo)
    _escribir(bat / "status", "Discharging\n")
    _escribir(bat / "power_now", "10000000\n")     # 10 W
    texto = " ".join(sistema_info.describir_consumo(esperar=0.0))
    assert "10.0 W" in texto and "7.2 kWh por mes" in texto   # 10 W * 720 h = 7,2 kWh


def test_texto_avisa_si_el_sensor_existe_pero_no_se_puede_leer(rapl, monkeypatch):
    _escribir(rapl / "energy_uj", "100\n")
    monkeypatch.setattr(sistema_info, "potencia_cpu", lambda esperar=1.0: None)  # simula "Permission denied"
    assert any("solo root puede leerlo" in l for l in sistema_info.describir_consumo())


def test_comando_consumo_del_bot(bot_listo):
    texto = telegram_bot.responder("/consumo", CHAT)
    assert texto.startswith("Consumo del equipo") and "enchufado" in texto
    assert telegram_bot.responder("/consumo", "999") is None

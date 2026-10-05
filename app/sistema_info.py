"""
Datos del equipo donde corre el servidor (batería, temperatura, carga, RAM,
Wi-Fi, discos), leídos de /sys y /proc con sólo la librería estándar.

Dentro de un contenedor Docker con `network_mode: host` /proc y /sys muestran
los datos del equipo real, no los del contenedor. HOST_SYS y HOST_PROC permiten
apuntar a otra raíz (por ejemplo si se montan en /host/sys) y probar con
carpetas falsas. Todo falla en silencio: si un dato no existe en este equipo
(un servidor sin batería, una tablet sin sensor de temperatura) simplemente
no se muestra.
"""
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional, Tuple


def _sys() -> Path:
    return Path(os.environ.get("HOST_SYS") or "/sys")


def _proc() -> Path:
    return Path(os.environ.get("HOST_PROC") or "/proc")


def _leer(ruta: Path) -> Optional[str]:
    try:
        return ruta.read_text().strip()
    except Exception:
        return None


# ─── Batería y corriente ──────────────────────────────────────────────────────

def _fuentes() -> Tuple[Optional[Path], Optional[Path]]:
    """(carpeta de la batería, carpeta del cargador) en power_supply."""
    base = _sys() / "class" / "power_supply"
    try:
        carpetas = sorted(base.iterdir())
    except Exception:
        return None, None
    bat = red = None
    for d in carpetas:
        tipo = _leer(d / "type")
        if tipo == "Battery" and bat is None and _leer(d / "present") != "0":
            bat = d
        elif tipo == "Mains" and red is None:
            red = d
    return bat, red


def bateria() -> Optional[dict]:
    """{'porcentaje': int|None, 'estado': str|None, 'enchufado': bool|None} o
    None si el equipo no tiene batería."""
    bat, red = _fuentes()
    if bat is None:
        return None
    capacidad = _leer(bat / "capacity")
    enchufado = None
    if red is not None:
        online = _leer(red / "online")
        if online in ("0", "1"):
            enchufado = online == "1"
    return {
        "porcentaje": int(capacidad) if capacidad and capacidad.isdigit() else None,
        "estado": _leer(bat / "status"),
        "enchufado": enchufado,
    }


_ESTADOS_BATERIA = {
    "Charging": "cargando",
    "Discharging": "DESCARGANDO (sin corriente externa)",
    "Full": "carga completa",
    "Not charging": "conectada pero sin cargar (límite de carga o ya está llena)",
}


def describir_bateria(b: dict) -> str:
    pct = "{}%".format(b["porcentaje"]) if b.get("porcentaje") is not None else "?"
    estado = _ESTADOS_BATERIA.get(b.get("estado") or "", "estado desconocido")
    texto = f"{pct}, {estado}"
    if b.get("enchufado") is not None:
        texto += " · " + ("enchufado" if b["enchufado"] else "SIN enchufar")
    return texto


def evaluar_energia(estaba_en_bateria: Optional[bool], b: Optional[dict], umbral_bajo: int = 20) -> Tuple[Optional[bool], List[str]]:
    """Dado el estado anterior y la lectura actual, devuelve (nuevo_estado,
    avisos). Se avisa sólo al cambiar: se cortó la luz, volvió, o la batería
    quedó baja mientras sigue sin corriente. `estaba_en_bateria` None = primera
    lectura (no avisa por el estado inicial)."""
    if b is None:
        return estaba_en_bateria, []
    en_bateria = b.get("estado") == "Discharging"
    pct = b.get("porcentaje")
    avisos = []
    if estaba_en_bateria is not None:
        if en_bateria and not estaba_en_bateria:
            avisos.append(f"Se cortó la luz: el servidor sigue andando con la batería ({pct}%).")
        elif not en_bateria and estaba_en_bateria:
            avisos.append(f"Volvió la corriente (batería {pct}%).")
    return en_bateria, avisos


# ─── Temperatura, carga, RAM, uptime ──────────────────────────────────────────

def temperatura_cpu() -> Optional[float]:
    base = _sys() / "class" / "thermal"
    candidatas = []
    try:
        zonas = sorted(base.glob("thermal_zone*"))
    except Exception:
        return None
    for z in zonas:
        temp = _leer(z / "temp")
        if temp and temp.lstrip("-").isdigit():
            candidatas.append(((_leer(z / "type") or ""), int(temp) / 1000.0))
    if not candidatas:
        return None
    for tipo, grados in candidatas:
        if "pkg_temp" in tipo or "cpu" in tipo.lower():
            return grados
    return max(g for _, g in candidatas)


def memoria() -> Optional[Tuple[int, int]]:
    """(total_mb, disponible_mb)."""
    texto = _leer(_proc() / "meminfo")
    if not texto:
        return None
    valores = {}
    for linea in texto.splitlines():
        partes = linea.split()
        if len(partes) >= 2 and partes[0] in ("MemTotal:", "MemAvailable:"):
            valores[partes[0]] = int(partes[1]) // 1024
    if "MemTotal:" in valores and "MemAvailable:" in valores:
        return valores["MemTotal:"], valores["MemAvailable:"]
    return None


def carga() -> Optional[Tuple[float, float, float]]:
    texto = _leer(_proc() / "loadavg")
    try:
        a, b, c = texto.split()[:3]
        return float(a), float(b), float(c)
    except Exception:
        return None


def uptime_segundos() -> Optional[float]:
    texto = _leer(_proc() / "uptime")
    try:
        return float(texto.split()[0])
    except Exception:
        return None


def wifi() -> Optional[Tuple[str, int]]:
    """(interfaz, nivel de señal en dBm) de la primera interfaz inalámbrica."""
    texto = _leer(_proc() / "net" / "wireless")
    if not texto:
        return None
    for linea in texto.splitlines()[2:]:
        if ":" not in linea:
            continue
        iface, _, resto = linea.partition(":")
        campos = resto.split()
        try:
            return iface.strip(), int(float(campos[2]))
        except Exception:
            continue
    return None


def calidad_wifi(dbm: int) -> str:
    if dbm >= -60:
        return "buena"
    if dbm >= -70:
        return "aceptable"
    return "débil"


# ─── Consumo eléctrico ────────────────────────────────────────────────────────

def _numero(ruta: Path) -> Optional[int]:
    texto = _leer(ruta)
    try:
        return int(texto)
    except Exception:
        return None


def consumo_bateria() -> Optional[float]:
    """Vatios que está entregando la batería ahora. Sólo equivale al consumo
    TOTAL del equipo cuando está descargando (sin cargador); enchufado, la
    batería no informa lo que gasta el equipo, así que devuelve None."""
    bat, _ = _fuentes()
    if bat is None or _leer(bat / "status") != "Discharging":
        return None
    microvatios = _numero(bat / "power_now")
    if not microvatios:
        corriente, voltaje = _numero(bat / "current_now"), _numero(bat / "voltage_now")
        if corriente and voltaje:
            microvatios = abs(corriente) * voltaje / 1e6  # µA × µV / 1e6 = µW
    return abs(microvatios) / 1e6 if microvatios else None


def _dormir(segundos: float) -> None:
    time.sleep(segundos)


RAPL_DIR = "intel-rapl:0"  # los tests lo cambian: Windows no permite ":" en nombres de carpeta


def _ruta_rapl() -> Path:
    return _sys() / "class" / "powercap" / RAPL_DIR


def potencia_cpu(esperar: float = 1.0) -> Optional[float]:
    """Vatios que consume el procesador (paquete), promediados en `esperar`
    segundos con el contador de energía RAPL. Ese archivo es sólo de root en la
    mayoría de los equipos: sin permiso devuelve None."""
    if esperar <= 0:
        return None
    base = _ruta_rapl()
    e1 = _numero(base / "energy_uj")
    if e1 is None:
        return None
    _dormir(esperar)
    e2 = _numero(base / "energy_uj")
    if e2 is None:
        return None
    if e2 < e1:  # el contador llegó a su máximo y volvió a cero
        rango = _numero(base / "max_energy_range_uj")
        if not rango:
            return None
        e2 += rango
    return (e2 - e1) / 1e6 / esperar


def describir_consumo(esperar: float = 1.0) -> List[str]:
    """Líneas de texto sobre el consumo, diciendo siempre qué se pudo medir y
    qué no (nunca se inventa un número)."""
    lineas = []
    total = consumo_bateria()
    if total is not None:
        kwh_mes = total * 24 * 30 / 1000
        lineas.append(f"• Consumo total del equipo: {total:.1f} W (medido en la batería; a este ritmo ≈ {kwh_mes:.1f} kWh por mes)")
    elif bateria() is not None:
        lineas.append("• Consumo total del equipo: no se puede medir enchufado (la batería solo lo informa al descargarse)")
    cpu = potencia_cpu(esperar)
    if cpu is not None:
        lineas.append(f"• Consumo de la CPU: {cpu:.1f} W (solo el procesador, no todo el equipo)")
    elif (_ruta_rapl() / "energy_uj").exists():
        lineas.append("• Consumo de la CPU: el sensor existe, pero solo root puede leerlo")
    return lineas


# ─── Discos ───────────────────────────────────────────────────────────────────

def discos(rutas: List[Tuple[str, str]]) -> List[Tuple[str, float, float]]:
    """[(nombre, libre_gb, total_gb)] para cada (nombre, ruta) que exista.
    Si dos rutas están en el mismo disco se muestra una sola."""
    vistos = set()
    salida = []
    for nombre, ruta in rutas:
        try:
            dev = os.stat(ruta).st_dev
            if dev in vistos:
                continue
            vistos.add(dev)
            uso = shutil.disk_usage(ruta)
            salida.append((nombre, uso.free / 1024 ** 3, uso.total / 1024 ** 3))
        except Exception:
            continue
    return salida


def duracion(segundos: float) -> str:
    segundos = int(segundos)
    d, resto = divmod(segundos, 86400)
    h, resto = divmod(resto, 3600)
    m = resto // 60
    return (f"{d}d " if d else "") + f"{h}h {m}m"

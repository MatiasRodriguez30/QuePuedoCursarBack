#!/usr/bin/env bash
# OPCIONAL: deja que el bot de Telegram lea el consumo de la CPU (contador RAPL)
# sin ser root. Correr con:  sudo bash deploy/servidor/permitir-lectura-rapl.sh
#
# Qué hace: cambia el permiso del archivo
#   /sys/class/powercap/intel-rapl:0/energy_uj
# de "solo root" a "lectura para todos", y lo repite en cada arranque con una
# regla de systemd-tmpfiles (los permisos de /sys no sobreviven a un reinicio).
#
# Costo de seguridad (por eso viene restringido a root por defecto): quien tenga
# una cuenta en la máquina puede observar el consumo de la CPU y, con técnicas
# avanzadas ("PLATYPUS"), deducir información de lo que se está ejecutando. En un
# servidor casero de una sola persona, con SSH solo por llave, el riesgo es bajo,
# pero es una decisión tuya. Para deshacerlo:
#   sudo rm /etc/tmpfiles.d/rapl-lectura.conf && sudo chmod 400 /sys/class/powercap/intel-rapl:0/energy_uj
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Correlo con sudo:  sudo bash $0"; exit 1
fi

ARCHIVO=/sys/class/powercap/intel-rapl:0/energy_uj
[ -e "$ARCHIVO" ] || { echo "Este equipo no tiene el sensor RAPL ($ARCHIVO). No hay nada que hacer."; exit 1; }

cat > /etc/tmpfiles.d/rapl-lectura.conf <<'EOF'
# Permite leer el contador de energía de la CPU para medir el consumo (ver
# deploy/servidor/permitir-lectura-rapl.sh en el repo de Qué Puedo Cursar).
z /sys/class/powercap/intel-rapl:0/energy_uj 0444 root root -
EOF

systemd-tmpfiles --create /etc/tmpfiles.d/rapl-lectura.conf
ls -l "$ARCHIVO"
echo
echo "Listo. Si después de reiniciar el equipo el bot vuelve a decir que solo root puede leer el sensor,"
echo "avisale a Claude: puede que el módulo se cargue después de aplicar la regla."

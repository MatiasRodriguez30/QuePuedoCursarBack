#!/usr/bin/env bash
# Auto-deploy y vigilancia de Qué Puedo Cursar en el servidor casero.
# Reemplaza lo que hacía deploy/tablet_run.sh en la tablet. Lo corre cron cada
# minuto como el usuario del servidor (sin sudo):
#
#   * * * * * ~/proyectos/QuePuedoCursarBack/deploy/servidor/vigilar.sh >> ~/proyectos/vigilar.log 2>&1
#
# 1) Si hay commits nuevos en origin/main: git reset --hard, `docker compose up
#    -d --build` y aviso por Telegram (deploy OK o falló).
# 2) Revisa que la API esté "healthy" y el túnel "running". Si no, lo levanta de
#    nuevo y avisa UNA vez al caer y UNA al volver (no una vez por minuto).
#
# Los avisos van por Telegram con deploy/notificar.py (usa el .env; si no hay
# token no hace nada). No usar `pkill -f` ni editar código acá: todo cambio entra
# por git (rama -> PR -> merge a main) y este script lo toma solo en ~1 minuto.
set -u

cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
exec 9>/tmp/qpc-vigilar.lock
flock -n 9 || exit 0   # si la corrida anterior sigue, no se pisan

ESTADO="$HOME/.qpc-vigilar-estado"
log() { echo "[$(date '+%F %T')] $*"; }
avisar() { python3 deploy/notificar.py "$*" >/dev/null 2>&1 || true; }

# ── 1) Auto-deploy ────────────────────────────────────────────────────────────
if git fetch -q origin main 2>/dev/null; then
  LOCAL=$(git rev-parse HEAD)
  REMOTO=$(git rev-parse origin/main)
  if [ "$LOCAL" != "$REMOTO" ]; then
    log "Commits nuevos ($LOCAL -> $REMOTO). Desplegando..."
    git reset -q --hard origin/main
    if docker compose up -d --build > /tmp/qpc-deploy.log 2>&1; then
      log "Deploy completo."
      avisar "Deploy OK: $(git log -1 --format='%h %s')"
    else
      log "FALLÓ el deploy, ver /tmp/qpc-deploy.log"
      avisar "ALERTA: falló el deploy de $(git log -1 --format='%h %s'). Revisar /tmp/qpc-deploy.log en el servidor."
    fi
  fi
fi

# ── 2) Vigilancia de los contenedores ─────────────────────────────────────────
problema=""
api=$(docker compose ps -q api 2>/dev/null | head -1)
tunel=$(docker compose ps -q tunel 2>/dev/null | head -1)

if [ -z "$api" ] || [ "$(docker inspect -f '{{.State.Status}}' "$api" 2>/dev/null)" != "running" ]; then
  problema="la API no está corriendo"
elif [ "$(docker inspect -f '{{.State.Health.Status}}' "$api" 2>/dev/null)" = "unhealthy" ]; then
  problema="la API está colgada (unhealthy)"
fi
if [ -z "$tunel" ] || [ "$(docker inspect -f '{{.State.Status}}' "$tunel" 2>/dev/null)" != "running" ]; then
  problema="${problema:+$problema; }el túnel no está corriendo"
fi

previo=$(cat "$ESTADO" 2>/dev/null || true)
if [ -n "$problema" ]; then
  log "PROBLEMA: $problema. Intentando recuperar..."
  if echo "$problema" | grep -q "colgada"; then
    docker compose restart api >/dev/null 2>&1
  else
    docker compose up -d >/dev/null 2>&1
  fi
  if [ -z "$previo" ]; then
    avisar "ALERTA: $problema. Intentando levantarlo de nuevo..."
  fi
  echo "$problema" > "$ESTADO"
elif [ -n "$previo" ]; then
  log "Recuperado."
  avisar "Qué Puedo Cursar volvió a funcionar (antes: $previo)."
  rm -f "$ESTADO"
fi

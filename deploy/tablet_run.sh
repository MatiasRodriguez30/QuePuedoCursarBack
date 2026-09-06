#!/data/data/com.termux/files/usr/bin/bash
# ─────────────────────────────────────────────────────────────────────────
# Corre el backend en la tablet (Termux) y lo mantiene actualizado solo:
#   - Expone el puerto 8000 a internet con un Cloudflare Tunnel nombrado
#     (URL fija, no cambia entre reinicios).
#   - Cada CHECK_INTERVAL segundos revisa si hay commits nuevos en el
#     remoto; si los hay, hace pull, reinstala dependencias si cambió
#     requirements.txt, y reinicia uvicorn (el túnel no se reinicia).
#
# Requiere que ya exista ~/QuePuedoCursarBack/cloudflared_config.yml
# (no se versiona: es específico de esta tablet). Ver README para crearlo.
#
# Uso:
#   cd ~/QuePuedoCursarBack
#   nohup bash deploy/tablet_run.sh > deploy.out 2>&1 &
#   disown
#
# Para que sobreviva a que cierres Termux, corré antes:
#   termux-wake-lock
# y desactivá la optimización de batería para Termux en Ajustes de Android.
# ─────────────────────────────────────────────────────────────────────────
set -u

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${DEPLOY_BRANCH:-main}"
CHECK_INTERVAL="${CHECK_INTERVAL:-60}"
PORT="${PORT:-8000}"
VENV_DIR="$REPO_DIR/venv"
CF_CONFIG="$REPO_DIR/cloudflared_config.yml"
PID_UVICORN="$REPO_DIR/.uvicorn.pid"
PID_TUNNEL="$REPO_DIR/.cloudflared.pid"
LOG_UVICORN="$REPO_DIR/uvicorn.log"
LOG_TUNNEL="$REPO_DIR/cloudflared.log"

cd "$REPO_DIR" || exit 1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

ensure_venv() {
  if [ ! -d "$VENV_DIR" ]; then
    log "Creando virtualenv..."
    python3 -m venv "$VENV_DIR"
  fi
  "$VENV_DIR/bin/pip" install -q -r requirements.txt
}

start_uvicorn() {
  if [ -f "$PID_UVICORN" ] && kill -0 "$(cat "$PID_UVICORN")" 2>/dev/null; then
    log "Deteniendo uvicorn anterior (pid $(cat "$PID_UVICORN"))..."
    kill "$(cat "$PID_UVICORN")" 2>/dev/null
    sleep 2
  fi
  log "Arrancando uvicorn en :$PORT ..."
  nohup "$VENV_DIR/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" >> "$LOG_UVICORN" 2>&1 &
  echo $! > "$PID_UVICORN"
}

start_tunnel_if_needed() {
  if [ -f "$PID_TUNNEL" ] && kill -0 "$(cat "$PID_TUNNEL")" 2>/dev/null; then
    return 0 # ya está corriendo, no necesita reiniciarse por un redeploy de la app
  fi
  if [ ! -f "$CF_CONFIG" ]; then
    log "ADVERTENCIA: no existe $CF_CONFIG, no se puede levantar el túnel (ver README)."
    return 1
  fi
  log "Arrancando Cloudflare Tunnel..."
  : > "$LOG_TUNNEL"
  # cloudflared es un binario Go estático: no puede resolver DNS ni validar TLS
  # usando los mecanismos nativos de Android. proot le "inyecta" un
  # /etc/resolv.conf y un bundle de certificados CA sin necesitar root.
  nohup proot \
    -b "$PREFIX/etc/resolv.conf:/etc/resolv.conf" \
    -b "$PREFIX/etc/tls/cert.pem:/etc/ssl/certs/ca-certificates.crt" \
    cloudflared tunnel --config "$CF_CONFIG" run >> "$LOG_TUNNEL" 2>&1 &
  echo $! > "$PID_TUNNEL"
  sleep 3
  if kill -0 "$(cat "$PID_TUNNEL")" 2>/dev/null; then
    log "Túnel iniciado. Hostname configurado en $CF_CONFIG"
  else
    log "El túnel no arrancó, revisá $LOG_TUNNEL"
  fi
}

ensure_venv
start_uvicorn
start_tunnel_if_needed

log "Sirviendo. Chequeando updates de git cada ${CHECK_INTERVAL}s (rama: $BRANCH)."

while true; do
  sleep "$CHECK_INTERVAL"

  # Si cloudflared murió (red caída, etc.), levantarlo de nuevo.
  if ! kill -0 "$(cat "$PID_TUNNEL" 2>/dev/null)" 2>/dev/null; then
    log "cloudflared no está corriendo, reintentando..."
    start_tunnel_if_needed
  fi

  git fetch origin "$BRANCH" --quiet 2>>"$LOG_UVICORN"
  LOCAL=$(git rev-parse HEAD)
  REMOTE=$(git rev-parse "origin/$BRANCH")

  if [ "$LOCAL" != "$REMOTE" ]; then
    log "Nuevos commits detectados ($LOCAL -> $REMOTE). Actualizando..."
    REQS_BEFORE=$(md5sum requirements.txt 2>/dev/null)
    git reset --hard "origin/$BRANCH"
    REQS_AFTER=$(md5sum requirements.txt 2>/dev/null)

    if [ "$REQS_BEFORE" != "$REQS_AFTER" ]; then
      log "requirements.txt cambió, reinstalando dependencias..."
      "$VENV_DIR/bin/pip" install -q -r requirements.txt
    fi

    start_uvicorn
    log "Redeploy completo."
  fi
done

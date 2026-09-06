#!/data/data/com.termux/files/usr/bin/bash
# ─────────────────────────────────────────────────────────────────────────
# Corre el backend en la tablet (Termux) y lo mantiene actualizado solo:
#   - Expone el puerto 8000 a internet con un Cloudflare Quick Tunnel.
#   - Cada CHECK_INTERVAL segundos revisa si hay commits nuevos en el
#     remoto; si los hay, hace pull, reinstala dependencias si cambió
#     requirements.txt, y reinicia uvicorn.
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

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${DEPLOY_BRANCH:-main}"
CHECK_INTERVAL="${CHECK_INTERVAL:-60}"
PORT="${PORT:-8000}"
VENV_DIR="$REPO_DIR/venv"
PID_UVICORN="$REPO_DIR/.uvicorn.pid"
PID_TUNNEL="$REPO_DIR/.cloudflared.pid"
LOG_UVICORN="$REPO_DIR/uvicorn.log"
LOG_TUNNEL="$REPO_DIR/cloudflared.log"
TUNNEL_URL_FILE="$REPO_DIR/tunnel_url.txt"

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
    return 0 # ya está corriendo, el túnel no necesita reiniciarse por un redeploy de la app
  fi
  log "Arrancando Cloudflare Quick Tunnel..."
  : > "$LOG_TUNNEL"
  nohup cloudflared tunnel --url "http://localhost:$PORT" --logfile "$LOG_TUNNEL" >> "$LOG_TUNNEL" 2>&1 &
  echo $! > "$PID_TUNNEL"

  # Esperar a que cloudflared imprima la URL pública (trycloudflare.com) y guardarla.
  for _ in $(seq 1 20); do
    sleep 1
    URL=$(grep -oE 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' "$LOG_TUNNEL" | head -1)
    if [ -n "$URL" ]; then
      echo "$URL" > "$TUNNEL_URL_FILE"
      log "Túnel público: $URL"
      return 0
    fi
  done
  log "No se pudo leer la URL del túnel todavía, revisá $LOG_TUNNEL"
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

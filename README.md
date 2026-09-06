# Qué puedo cursar — Backend

API REST + WebSocket para gestionar el plan de estudios universitario.

El frontend (React) vive en un repo aparte, desplegado en Vercel:
[QuePuedoCursarFront](https://github.com/MatiasRodriguez30/QuePuedoCursarFront).

## Stack
- **FastAPI** · **SQLAlchemy** · **SQLite** · **Pydantic v2** · **Uvicorn**

## Desarrollo local

```bash
python -m venv venv
venv\Scripts\activate        # Windows / source venv/bin/activate en Linux
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Docs interactivas: http://localhost:8000/docs
- WebSocket: `ws://localhost:8000/ws`

### Cargar el plan de estudios de ejemplo

Con el server corriendo:

```bash
python scripts/seed_plan.py
```

Carga las 37 materias del Plan 2023 de Ing. en Sistemas (UTN) + 10 electivas
del 2do semestre 2026, con todas sus correlativas. Es seguro re-correrlo:
borra lo que haya antes de recargar.

## Despliegue en la tablet (Termux) + Cloudflare Tunnel

La idea: el backend corre en una tablet Android vía Termux, expuesto a
internet con un [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
(sin necesidad de abrir puertos en el router). Cuando alguien hace `push` a
`main`, la tablet lo detecta solo y redeploya.

### Instalación inicial en la tablet

```bash
pkg update -y && pkg install -y git python proot ca-certificates

git clone https://github.com/MatiasRodriguez30/QuePuedoCursarBack.git
cd QuePuedoCursarBack

# cloudflared no está en los repos de Termux: bajar el binario ARM a mano.
# Para tablets de 32 bits (armv7l/armeabi-v7a):
curl -L -o $PREFIX/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm
# Para tablets de 64 bits (aarch64/arm64), usar en cambio:
#   .../cloudflared-linux-arm64
chmod +x $PREFIX/bin/cloudflared

termux-wake-lock   # evita que Android mate el proceso al apagar pantalla
```

Después, en Ajustes de Android → Batería → Termux, desactivá la optimización
de batería (si no, el sistema puede matar el proceso igual).

⚠️ **Nota Termux/ARM**: `cloudflared` es un binario Go estático y no puede usar
la resolución DNS ni la validación TLS nativas de Android (a diferencia de
`curl`/`ping`, que sí funcionan). Sin root no se puede escribir el `/etc/`
real del sistema para arreglarlo directamente, así que se usa `proot` (sin
privilegios) para "inyectarle" un `resolv.conf` y un bundle de certificados
CA propios de Termux. El script `deploy/tablet_run.sh` ya hace esto
automáticamente en cada arranque del túnel — no hace falta pensarlo dos veces,
pero si corrés `cloudflared` a mano fuera del script, envolvelo así:

```bash
proot -b $PREFIX/etc/resolv.conf:/etc/resolv.conf \
      -b $PREFIX/etc/tls/cert.pem:/etc/ssl/certs/ca-certificates.crt \
      cloudflared <comando>
```

### Configurar el túnel nombrado (URL fija)

A diferencia de un Quick Tunnel (URL aleatoria que cambia en cada reinicio),
un túnel nombrado con un dominio propio en Cloudflare da una URL **fija para
siempre**. Se hace una sola vez:

```bash
# 1. Login (abre una URL: hay que autorizarla desde un navegador).
#    Si la tablet ya tiene un cert.pem de otro proyecto con la misma cuenta
#    de Cloudflare, este paso se puede saltear.
proot -b $PREFIX/etc/resolv.conf:/etc/resolv.conf \
      -b $PREFIX/etc/tls/cert.pem:/etc/ssl/certs/ca-certificates.crt \
      cloudflared tunnel login

# 2. Crear el túnel (anotar el UUID que devuelve)
proot -b $PREFIX/etc/resolv.conf:/etc/resolv.conf \
      -b $PREFIX/etc/tls/cert.pem:/etc/ssl/certs/ca-certificates.crt \
      cloudflared tunnel create quepuedocursar

# 3. Crear cloudflared_config.yml (NO se versiona, es específico de esta
#    tablet) con el UUID del paso anterior:
cat > cloudflared_config.yml << 'EOF'
tunnel: <UUID-DEL-TUNEL>
credentials-file: /data/data/com.termux/files/home/.cloudflared/<UUID-DEL-TUNEL>.json
ingress:
  - hostname: <tu-subdominio>.<tu-dominio>
    service: http://127.0.0.1:8000
  - service: http_status:404
EOF

# 4. Rutear el subdominio al túnel (-f fuerza el reemplazo si ya existía)
proot -b $PREFIX/etc/resolv.conf:/etc/resolv.conf \
      -b $PREFIX/etc/tls/cert.pem:/etc/ssl/certs/ca-certificates.crt \
      cloudflared tunnel --config cloudflared_config.yml route dns -f \
      quepuedocursar <tu-subdominio>.<tu-dominio>
```

⚠️ Si la tablet ya tenía **otro** `~/.cloudflared/config.yml` de un proyecto
anterior, especificá siempre `--config cloudflared_config.yml` explícitamente
(como en los comandos de arriba) — sin eso, `cloudflared` carga por defecto
ese config viejo y puede rutear el DNS al túnel equivocado.

### Arrancar todo (server + túnel + auto-deploy)

```bash
nohup bash deploy/tablet_run.sh > deploy.out 2>&1 &
disown
```

Esto:
1. Crea el virtualenv e instala dependencias si hace falta.
2. Levanta `uvicorn` en el puerto 8000.
3. Levanta el Cloudflare Tunnel nombrado usando `cloudflared_config.yml`.
4. Cada 60s chequea si hay commits nuevos en `origin/main`; si los hay, hace
   `git reset --hard`, reinstala dependencias si cambió `requirements.txt`,
   y reinicia `uvicorn` — sin downtime del túnel.

### Logs y control manual

```bash
tail -f uvicorn.log        # logs del backend
tail -f cloudflared.log    # logs del túnel
kill $(cat .uvicorn.pid)   # frenar el backend a mano
kill $(cat .cloudflared.pid)  # frenar el túnel a mano
```

## WebSocket

Cualquier cliente puede conectarse a `/ws`. Cada vez que alguien cambia un
estado, agrega o elimina una materia/prerequisito, o cambia el año/cuatrimestre
configurado, **todos los clientes conectados reciben el evento** en tiempo real.

### Formato del mensaje

```json
{
  "event": "estado_actualizado",
  "data": { ... }
}
```

### Eventos

| Evento | Trigger |
|---|---|
| `materia_creada` | POST /materias |
| `materia_actualizada` | PUT /materias/{id} |
| `materia_eliminada` | DELETE /materias/{id} |
| `prerequisito_creado` | POST /prerequisitos |
| `prerequisito_eliminado` | DELETE /prerequisitos/{id} |
| `estado_actualizado` | PUT /estados/{materia_id} |
| `estados_reseteados` | POST /estados/reset |
| `config_actualizada` | PUT /config |

## Estados de materia

| Estado | Significado |
|---|---|
| `NO_CURSADA` | No se cursó (puede estar bloqueada por prerequisitos) |
| `CURSANDO` | Se está cursando actualmente, resultado pendiente |
| `REGULAR` | Se cursó, pendiente de rendir el final |
| `PROMOCIONADA` | Aprobada (cursada + final aprobado o promoción directa) |

## Regla de prerequisitos

| Tipo | Condición |
|---|---|
| `REGULARIZADA` | La materia requerida debe estar en `REGULAR` o `PROMOCIONADA` |
| `APROBADA` | La materia requerida debe estar en `PROMOCIONADA` |

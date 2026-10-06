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

## Verificación y CI

Antes de pedir un merge:

```bash
pip install -r requirements-dev.txt
python scripts/verify.py
```

Busca construcciones que no funcionan en **Python 3.8** (la versión de la
tablet: `X | None` o `list[int]` en anotaciones, `match`, `asyncio.to_thread`,
`removeprefix`, `zoneinfo`, `datetime.UTC`) y corre todos los tests. Tiene que
terminar en `VERIFICACION: OK`.

En GitHub, cada PR y cada push a `main` corren automáticamente
(`.github/workflows/ci.yml`): tests en un **Python 3.8 real**, tests en
Python 3.12 y búsqueda de secretos (gitleaks). La rama `main` está protegida:
no se puede pushear directo ni mergear un PR si alguno de esos controles falla.

## Bot de Telegram (opcional)

Un bot personal que corre **dentro del mismo proceso de uvicorn** (un hilo con long polling: no necesita URL pública, túnel ni puertos abiertos, ni dependencias nuevas, solo `urllib`). Sin `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` no arranca y todo sigue igual.

| Comando | Qué responde |
|---|---|
| `/hoy`, `/manana` | Agenda del día: eventos institucionales + los personales **de tu cuenta** (nunca los de otro usuario) |
| `/cursar` | Materias que podés cursar ahora (mismas reglas de correlatividad que la app) |
| `/pc` | El equipo: batería (si está enchufado/cargando), temperatura de la CPU, carga, RAM, discos, señal Wi-Fi, consumo y uptime |
| `/consumo` | Cuánta energía gasta ahora (ver nota abajo) |
| `/servicios` | Qué Puedo Cursar: API, base de datos, dominio público (prueba de punta a punta), recordatorio de las 21:00, mails y conexiones en vivo |
| `/estado` | `/servicios` y `/pc` juntos |

Además manda por Telegram: el recordatorio de las 21:00 (el mismo contenido que el mail, solo para tu cuenta) y los avisos del servidor: arranque, deploy OK, y caída/recuperación de uvicorn o del túnel (`deploy/tablet_run.sh`; si uvicorn muere ahora se reinicia solo y avisa en la 1.ª y la 5.ª caída seguida).

**Consumo:** el consumo *total* del equipo solo se puede medir cuando la batería se está descargando (la batería informa la corriente y el voltaje; enchufado no informa nada útil). El consumo del procesador sale del contador RAPL, que en Linux es solo de root: para que el bot lo lea hay que correr una vez `deploy/servidor/permitir-lectura-rapl.sh` con sudo (opcional; es un permiso de lectura sobre un contador de energía, con el pequeño costo de seguridad que se explica en el script).

**Alertas automáticas (equipos con batería):** avisa por Telegram cuando se corta la luz, cuando vuelve y cuando la batería baja del 20 % sin corriente. Un cambio solo se confirma si aparece en dos lecturas seguidas (cada 30 s) para evitar falsas alarmas.

**Alerta de temperatura:** avisa si la CPU se mantiene en 90 °C o más durante más de 2 minutos (un pico corto es normal: al empezar una carga el firmware deja subir la CPU unos 27 s antes de aplicar el límite de potencia), y avisa una vez más cuando baja de 80 °C. Una sola alerta por episodio.

**Seguridad:** el bot solo le hace caso al chat de `TELEGRAM_CHAT_ID`; cualquier otro mensaje se ignora sin respuesta. El token es una clave: va únicamente en el `.env` de la tablet (nunca en git ni en el chat).

**Configuración (una vez):**

1. En Telegram, hablá con `@BotFather` → `/newbot` y copiá el token.
2. En la tablet, agregá `TELEGRAM_BOT_TOKEN=...` y `TELEGRAM_USUARIO_EMAIL=tu-email-de-la-app` al `.env`.
3. Escribile "hola" a tu bot y corré `./venv/bin/python scripts/telegram_chat_id.py`; copiá el número a `TELEGRAM_CHAT_ID` en el `.env`.
4. Reiniciá la API para que lea el `.env`.

> Los avisos de caída solo pueden salir si la tablet tiene red: si se corta la luz o el Wi-Fi, no hay forma de que avise desde adentro. Para eso hace falta un monitor externo.

## Servidor casero (Docker)

Alternativa a la tablet: `compose.yml` levanta la API (Python 3.12) y el túnel de Cloudflare, ambos con `restart: unless-stopped`. Hace falta Docker con el plugin `compose`.

Archivos que **no** se versionan y hay que crear en el servidor: `.env` (igual que en la tablet), `cloudflared/config.yml` (ver `deploy/servidor/cloudflared-config.example.yml`) y `cloudflared/<uuid>.json` (credenciales del túnel). La base queda en `$DATOS_DIR/plan_estudios.db` (por defecto `./data`; en el servidor casero `DATOS_DIR` apunta al disco duro USB, y si ese disco no está montado el contenedor no arranca en vez de crear una base vacía en el SSD).

```bash
docker compose up -d --build    # levantar o actualizar
docker compose ps               # la api debe figurar "healthy"
docker compose logs -f api      # logs
docker compose restart api      # reiniciar solo la API
```

**Auto-deploy y vigilancia:** `deploy/servidor/vigilar.sh` (lo corre `cron` cada minuto, sin sudo) toma los commits nuevos de `origin/main`, reconstruye con `docker compose up -d --build` y avisa por Telegram si el deploy salió bien o falló; además revisa que la API esté `healthy` y el túnel corriendo, los levanta de nuevo si no, y avisa una vez al caer y una al volver. Se instala con:

```bash
git clone https://github.com/MatiasRodriguez30/QuePuedoCursarBack.git ~/proyectos/QuePuedoCursarBack   # o `git init` + `git remote add` + `git reset --hard origin/main` sobre una copia ya existente
(crontab -l 2>/dev/null; echo '* * * * * ~/proyectos/QuePuedoCursarBack/deploy/servidor/vigilar.sh >> ~/proyectos/vigilar.log 2>&1') | crontab -
```

Un merge a `main` queda desplegado en ~1 minuto, igual que antes en la tablet (con unos segundos de corte al reiniciar la API).

Cuidado: no correr la tablet y el servidor a la vez con el mismo túnel y bases distintas (Cloudflare repartiría los pedidos entre las dos). La tablet quedó retirada y su autoarranque desactivado.

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

### Que se levante solo si la tablet se reinicia

Termux no tiene forma nativa de arrancar procesos al bootear el dispositivo
(eso requeriría la app aparte **Termux:Boot**, no instalada). En cambio,
`~/.bashrc` en la tablet tiene este chequeo, que corre en **cualquier sesión
nueva** (abrir la app de Termux, o simplemente conectarse por SSH — el sshd
de Termux corre como servicio de fondo persistente):

```bash
# ~/.bashrc
QPC_DIR="$HOME/QuePuedoCursarBack"
if [ -d "$QPC_DIR" ] && ! pgrep -f 'deploy/tablet_run.sh' > /dev/null 2>&1; then
  termux-wake-lock >/dev/null 2>&1
  ( cd "$QPC_DIR" && nohup bash deploy/tablet_run.sh > deploy.out 2>&1 & disown )
  echo '[Qué Puedo Cursar] No estaba corriendo, lo arranqué solo.'
fi
```

Con esto, apenas alguien (vos, tu novia, o un `ssh` de chequeo) toca la
tablet después de un reinicio, todo se vuelve a levantar solo — no hace
falta acordarse de correr nada a mano. Sigue sin ser "apenas prende el
dispositivo" (para eso sí hace falta Termux:Boot), pero cubre el caso real
que nos pasó: un corte de luz o reinicio, y la primera conexión SSH lo repara.

### Logs y control manual

```bash
tail -f uvicorn.log        # logs del backend
tail -f cloudflared.log    # logs del túnel
kill $(cat .uvicorn.pid)   # frenar el backend a mano
kill $(cat .cloudflared.pid)  # frenar el túnel a mano
```

## WebSocket

Los clientes se conectan a `/ws?token=<token de sesión>`. Los datos compartidos
(materias, prerequisitos, agenda, carreras, configuración) se emiten a **todos**
los clientes conectados. El **progreso de cada usuario** (`estado_actualizado`,
`estados_reseteados`) llega **solo a los dispositivos de ese usuario**, y a su
grupo únicamente los logros (`logro_grupo`) y los cambios de la lista de
miembros (`grupo_miembros`).

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
| `estado_actualizado` | PUT /estados/{materia_id} (solo a los dispositivos del propio usuario) |
| `estados_reseteados` | POST /estados/reset (solo a los dispositivos del propio usuario) |
| `config_actualizada` | PUT /config |
| `logro_grupo` | Alguien del grupo aprobó o regularizó una materia (y comparte su progreso) |
| `grupo_miembros` | Alguien entró/salió, cambió su apodo o su preferencia, o se conectó/desconectó |
| `grupo_cursando` | Alguien del grupo empezó o dejó de cursar una materia (y comparte su progreso) |

## Grupos, logros en vivo y "quién cursa esto ahora"

Un grupo se crea con `POST /grupos` y se comparte con su **código de
invitación** (8 caracteres). Cada usuario está en a lo sumo un grupo.

| Endpoint | Descripción |
|---|---|
| `POST /grupos` `{nombre}` | Crea un grupo y te deja como primer miembro |
| `POST /grupos/unirse` `{codigo}` | Entra con el código (límite de 5 intentos por minuto) |
| `GET /grupos/mio` | Tu grupo con miembros y quién está en línea (404 si no tenés) |
| `PUT /grupos/mio/preferencias` `{comparte}` | Compartir o no tu progreso |
| `POST /grupos/salir` | Salís del grupo (si era el último, se elimina) |
| `GET /grupos/mio/cursando` | Qué materia está cursando cada miembro que comparte (lista `{materia_id, usuario_id, apodo}`) |
| `PUT /auth/apodo` `{apodo}` | Tu nombre visible (2 a 20 caracteres; por defecto "Cobayo N") |

`comparte` activado significa participar en ambos sentidos: tus logros y las
materias que cursás se anuncian al grupo, y recibís los suyos. Quien lo
desactiva sigue siendo miembro pero no envía ni recibe nada de eso. Marcar y
desmarcar la misma materia dentro de un minuto no repite el aviso de logro.

La columna `usuarios.apodo` se agrega sola al arrancar (`app/migraciones.py`,
con backup previo `plan_estudios.db.bak-preGrupos`).

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

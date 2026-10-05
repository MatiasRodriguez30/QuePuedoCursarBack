# Backend de Qué Puedo Cursar para el servidor casero (Ubuntu + Docker).
# Python 3.12 a propósito: el sistema trae 3.14, donde Pydantic v1 (pineado en
# requirements.txt) no funciona.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY deploy/notificar.py ./deploy/notificar.py

# uid 1000 = el usuario del servidor: así puede escribir el volumen ./data.
RUN useradd --uid 1000 --create-home app && mkdir /data && chown app /data
USER app

# La base vive en el volumen /data (ver compose.yml), no dentro de la imagen.
ENV DB_PATH=/data/plan_estudios.db
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=4)"

# Mismas opciones que tablet_run.sh: con --proxy-headers el rate limiting de
# /auth/* ve la IP real (X-Forwarded-For) y no la de cloudflared; se confía
# sólo en 127.0.0.1 (el túnel corre en la red del host, ver compose.yml).
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=127.0.0.1"]

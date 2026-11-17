# Product Rationalisation Agent - API and worker image (one image, two commands).
# Build from the repository root (manifest.json lives there):
#   docker build -f backend/Dockerfile -t pra-backend .
#   docker run --env-file .env -p 8000:8000 pra-backend                       # migrations, then API on :8000
#   docker run --env-file .env pra-backend python -m app.jobs.worker          # background jobs + outbox relay
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system pra && useradd --system --gid pra --home-dir /app --shell /usr/sbin/nologin pra

WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/scripts ./scripts
# app.main reads the manifest two levels above app/main.py: /app/app/main.py -> /manifest.json
COPY manifest.json /manifest.json

RUN pip install --no-cache-dir . \
    && mkdir -p /app/var/storage \
    && chown -R pra:pra /app /manifest.json

USER pra
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]

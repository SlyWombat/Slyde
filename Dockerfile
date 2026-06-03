# syntax=docker/dockerfile:1

# --- Stage 1: build the web UI ------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime (serves API + built SPA) -------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATIC_DIR=/app/static \
    DATABASE_URL=sqlite:////data/memento.db \
    BIND_HOST=0.0.0.0 \
    BIND_PORT=8080
WORKDIR /app

# Install the local packages. memento-core first so memento-backend's dependency on it
# resolves to the local build (not PyPI); its other deps come from PyPI.
COPY packages/memento-core ./packages/memento-core
COPY packages/memento-backend ./packages/memento-backend
RUN pip install ./packages/memento-core ./packages/memento-backend

COPY --from=web /web/dist ./static

RUN useradd --create-home app && mkdir -p /data && chown app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health').status==200 else 1)"

CMD ["memento-backend"]

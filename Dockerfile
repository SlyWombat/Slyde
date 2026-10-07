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
    CACHE_DIR=/data/cache \
    BIND_HOST=0.0.0.0 \
    BIND_PORT=8080 \
    HOME=/tmp
WORKDIR /app

# Install the local packages. memento-core first so slyde-backend's dependency on it
# resolves to the local build (not PyPI); its other deps come from PyPI.
COPY packages/memento-core ./packages/memento-core
COPY packages/slyde-backend ./packages/slyde-backend
RUN pip install ./packages/memento-core ./packages/slyde-backend

COPY --from=web /web/dist ./static

# Runs as a bare numeric uid, so the host can pick any uid with compose `user:` (#77): nothing is
# written outside /data, HOME is /tmp, and no /etc/passwd entry is needed. /data is pre-owned by
# the default uid only so a fresh named volume is writable out of the box.
RUN mkdir -p /data && chown 1000:1000 /data
USER 1000:1000
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health').status==200 else 1)"

CMD ["slyde"]

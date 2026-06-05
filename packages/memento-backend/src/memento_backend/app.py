"""FastAPI application factory: wires config, store, frame service, and Immich together."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .backends import ServedFrameBackend, get_backend
from .config import Settings, get_settings
from .firmware import FirmwareService
from .frames import FrameService, FrameUnavailable
from .immich import ImmichClient, ImmichError
from .jobs import JobManager
from .routers import firmware, frames, health, immich
from .scheduler import SyncScheduler
from .serving import PlaceholderDelivery, mount_served_backends
from .store import Store
from .sync import SyncService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(settings.sqlite_path)
        frame_service = FrameService(settings, store=store)

        def immich_factory() -> ImmichClient:
            return ImmichClient(settings.immich_base_url, settings.immich_api_key)

        sync_service = SyncService(settings, frame_service, store, immich_factory)
        scheduler = SyncScheduler(sync_service, settings.sync_interval_minutes)

        app.state.settings = settings
        app.state.store = store
        app.state.frame = frame_service
        app.state.immich_factory = immich_factory
        app.state.sync = sync_service
        app.state.scheduler = scheduler
        app.state.jobs = JobManager()
        app.state.firmware = FirmwareService(settings)
        # What a served (cloud) frame gets when it polls us. Placeholder until the Immich-backed
        # curation + processing-profile delivery lands (#8/#19/#23).
        app.state.frame_delivery = PlaceholderDelivery()
        scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    app = FastAPI(title="Memento Manager", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(FrameUnavailable)
    async def _frame_unavailable(_request: Request, exc: FrameUnavailable) -> JSONResponse:
        # The frame is offline / can't be resolved — a transient upstream condition, not a 500.
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ImmichError)
    async def _immich_error(_request: Request, exc: ImmichError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.middleware("http")
    async def _spa_cache_control(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Cache content-hashed assets forever, but always revalidate index.html so a new
        deploy is picked up without a manual hard-refresh."""
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        elif response.headers.get("content-type", "").startswith("text/html"):
            response.headers["cache-control"] = "no-cache"
        return response

    app.include_router(health.router, prefix="/api")
    app.include_router(frames.router, prefix="/api")
    app.include_router(immich.router, prefix="/api")
    app.include_router(firmware.router, prefix="/api")

    # Served (cloud) backends mount the endpoints their frames poll — at the cloud's own path (not
    # under /api), before the SPA catch-all. Driven by FRAME_BACKEND; nothing hardcoded.
    backend = get_backend(settings.frame_backend)
    if isinstance(backend, ServedFrameBackend):
        mount_served_backends(app, [backend])

    if settings.static_dir and Path(settings.static_dir).is_dir():
        # Serve the built SPA (and let client-side routing handle unknown paths).
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="spa")

    return app

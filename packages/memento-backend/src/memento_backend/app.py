"""FastAPI application factory: wires config, store, frame service, and Immich together."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .frames import FrameService
from .immich import ImmichClient
from .routers import frame, health, immich, sync
from .store import Store
from .sync import SyncService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(settings.sqlite_path)
        frame_service = FrameService(settings)

        def immich_factory() -> ImmichClient:
            return ImmichClient(settings.immich_base_url, settings.immich_api_key)

        app.state.settings = settings
        app.state.store = store
        app.state.frame = frame_service
        app.state.immich_factory = immich_factory
        app.state.sync = SyncService(settings, frame_service, store, immich_factory)
        yield

    app = FastAPI(title="Memento Manager", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router, prefix="/api")
    app.include_router(frame.router, prefix="/api")
    app.include_router(immich.router, prefix="/api")
    app.include_router(sync.router, prefix="/api")

    if settings.static_dir and Path(settings.static_dir).is_dir():
        # Serve the built SPA (and let client-side routing handle unknown paths).
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="spa")

    return app

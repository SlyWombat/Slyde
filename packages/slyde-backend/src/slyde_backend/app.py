"""FastAPI application factory: wires config, store, frame service, and Immich together."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .backends import ServedFrameBackend, get_backend
from .config import Settings, get_settings
from .delivery_service import DeliveryService
from .firmware import FirmwareService
from .folder_sync import FolderSyncService
from .frames import FrameService, FrameUnavailable, refresh_current_previews
from .imagecache import ImageCache
from .immich import ImmichClient, ImmichError
from .jobs import JobManager
from .library import FrameLibrary
from .previews import AssetPreviewCache
from .routers import assets, firmware, frames, health, immich
from .scheduler import SyncScheduler
from .serving import CachedImageDelivery, PlaceholderDelivery, mount_served_backends
from .store import Store
from .switchbot_service import SwitchBotService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(settings.sqlite_path)
        frame_service = FrameService(settings, store=store)

        def immich_factory() -> ImmichClient:
            return ImmichClient(settings.immich_base_url, settings.immich_api_key)

        # Cache of prepared (edited) images, ready to serve/push (#25). Served frames are handed a
        # cached image on poll; until the delivery service fills it, a placeholder is used.
        image_cache = ImageCache(settings.cache_dir)
        # Slyde's own canonical previews, kept per asset independent of any frame (like Immich).
        asset_previews = AssetPreviewCache(f"{settings.cache_dir}/previews")
        # Originals of photos pushed by the app (not in Immich), so they survive a cache eviction.
        uploads = ImageCache(f"{settings.cache_dir}/uploads")
        # The desired photo set per frame (curation), decoupled from delivery (#23).
        library = FrameLibrary(store, image_cache)
        # Live wiring (#25/#26): curation -> guaranteed-delivery queue -> prepare -> cache/push.
        delivery_service = DeliveryService(
            store, library, image_cache, frame_service, immich_factory, settings, uploads
        )
        # Keep-in-sync as per-folder bindings reconciled onto the delivery queue (#62).
        folder_sync = FolderSyncService(settings, store, library, delivery_service, immich_factory)

        # Opportunistically cache each connected frame's current image as its preview, so the
        # overview renders the live picture from cached bytes with no blocking per-card call (#68).
        async def _refresh_current_previews() -> None:
            await refresh_current_previews(frame_service, store, asset_previews)

        scheduler = SyncScheduler(
            folder_sync,
            settings.sync_interval_minutes,
            delivery_service,
            settings.delivery_interval_seconds,
            current_preview=_refresh_current_previews,
            current_preview_interval_seconds=settings.current_preview_interval_seconds,
        )

        app.state.settings = settings
        app.state.store = store
        app.state.frame = frame_service
        app.state.immich_factory = immich_factory
        app.state.folder_sync = folder_sync
        app.state.scheduler = scheduler
        app.state.jobs = JobManager()
        app.state.firmware = FirmwareService(settings)
        app.state.image_cache = image_cache
        app.state.asset_previews = asset_previews
        app.state.uploads = uploads
        app.state.frame_delivery = CachedImageDelivery(image_cache, fallback=PlaceholderDelivery())
        app.state.library = library
        app.state.delivery_service = delivery_service
        # SwitchBot AI Art Frames are account-scoped: this lists/registers them + reads live status
        # (the 'switchbot' backend pushes to SwitchBot's cloud; delivery rides the same queue, #64).
        app.state.switchbot = SwitchBotService(settings, store)
        scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    app = FastAPI(title="Slyde", version=__version__, lifespan=lifespan)

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
    app.include_router(assets.router, prefix="/api")
    app.include_router(firmware.router, prefix="/api")

    # Served (cloud) backends mount the endpoints their frames poll — at the cloud's own path (not
    # under /api), before the SPA catch-all. The hub mounts the primary backend if it's served, PLUS
    # any FRAME_SERVED_BACKENDS, so one hub drives both a connected frame (e.g. memento-lan) and a
    # polled one (e.g. sungale-cloud) over a shared registry/delivery queue. Nothing hardcoded.
    served: list[ServedFrameBackend] = []
    primary = get_backend(settings.frame_backend)
    if isinstance(primary, ServedFrameBackend):
        served.append(primary)
    for name in settings.served_backend_names:
        backend = get_backend(name)
        if not isinstance(backend, ServedFrameBackend):
            raise ValueError(f"FRAME_SERVED_BACKENDS entry {name!r} is not a served backend")
        if backend.name not in {b.name for b in served}:
            served.append(backend)
    if served:
        mount_served_backends(app, served)

    if settings.static_dir and Path(settings.static_dir).is_dir():
        # Serve the built SPA (and let client-side routing handle unknown paths).
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="spa")

    return app

"""Served-backend integration — mounting, frame identification, and delivery (issue #22).

A `ServedFrameBackend` (e.g. the Sungale cloud frame) is reached by the *frame polling a server we
run*, not by us connecting to it. This module is the framework that wires that up:

- ``mount_served_backends`` — include each served backend's router into the FastAPI app, at the path
  the frame expects (e.g. the impersonated cloud's ``/xiaowooya/api/v1``).
- ``resolve_or_register_served_frame`` — turn the frame-code a backend identified out of the request
  into a registered `Frame` (auto-registering on first contact, touching ``last_seen`` thereafter).
- ``FrameDelivery`` — the seam a backend calls to get the image to serve a frame. The real,
  Immich-backed + processing-profile delivery lands in #8/#19/#23; until then
  ``PlaceholderDelivery`` serves a generated placeholder so the mechanism is exercisable end-to-end.

Trust boundary: served endpoints are the *impersonated cloud* and are only as authenticated as the
frame-code the device presents. They are meant to be reachable only on the trusted LAN/IoT VLAN the
frame lives on (see ``docs/framework-design.md`` §2.2 and the eFrame deploy issue #10) — do not
expose them to the internet.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Protocol

from PIL import Image

from .frame import Frame
from .imagecache import ImageCache

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .backends import ServedFrameBackend
    from .store import Store

_PLACEHOLDER_SIZE = (800, 480)


class FrameDelivery(Protocol):
    """What to serve a frame when it polls. Implemented by the curation/processing layer (#23)."""

    async def image_for(self, frame: Frame) -> bytes | None: ...


class PlaceholderDelivery:
    """Default delivery until curation + processing + Immich are wired (#8/#19/#23).

    Serves a generated placeholder image, so a served backend's poll path can be exercised
    end-to-end before the real image pipeline exists.
    """

    async def image_for(self, frame: Frame) -> bytes | None:
        img = Image.new("RGB", _PLACEHOLDER_SIZE, (11, 14, 20))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()


class CachedImageDelivery:
    """Serve a frame the prepared image the hub already cached for it (issue #25).

    Reads from the processed-image cache (already smart-blurred/fitted/dithered, ready to send).
    When the frame has nothing cached yet, falls back to ``fallback`` (a placeholder) so the served
    path still responds. The cache is filled by the autonomous sync/processing service (#23/#19/#8).
    """

    def __init__(self, cache: ImageCache, *, fallback: FrameDelivery | None = None) -> None:
        self._cache = cache
        self._fallback = fallback

    async def image_for(self, frame: Frame) -> bytes | None:
        data = self._cache.current(frame.id)
        if data is not None:
            return data
        return await self._fallback.image_for(frame) if self._fallback is not None else None


def resolve_or_register_served_frame(store: Store, backend_name: str, frame_code: str) -> Frame:
    """Map an identified frame-code to a registered `Frame`, registering it on first contact."""
    existing = store.get_frame(frame_code)
    if existing is not None:
        store.touch_frame(frame_code)
        return existing
    frame = Frame.served(frame_code, backend=backend_name)
    store.upsert_frame(frame)
    store.touch_frame(frame_code)
    return store.get_frame(frame_code) or frame


def mount_served_backends(app: FastAPI, backends: list[ServedFrameBackend]) -> None:
    """Include each served backend's router so its frames can poll the manager."""
    for backend in backends:
        app.include_router(backend.router())

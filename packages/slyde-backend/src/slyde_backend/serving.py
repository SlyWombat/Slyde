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


def resolve_served_frame(store: Store, backend_name: str, *ids: str) -> Frame:
    """Resolve one served `Frame` from any of the ids a device is known by, registering on first
    contact and unifying its identities.

    The app refers to one frame by several ids depending on the endpoint (numeric frame_id /
    setting_id, device_id, serial), while the frame uses device_id — so a request may carry one or
    more. We map every id to a single canonical frame via the alias table:

    - if any id already resolves to a frame, that's the canonical one (the first, by caller order);
    - if several ids resolve to *different* frames, they're the same device under different ids, so
      we merge the rest into the first (``rekey_frame`` moves their library/queue/settings);
    - all presented ids are then linked to the canonical frame, so a future request using *any* of
      them resolves here — this opportunistically harvests linkage from requests carrying two ids.
    """
    candidates = [str(i) for i in ids if i]
    if not candidates:
        raise ValueError("no identifier presented")

    canonicals: list[str] = []
    for i in candidates:
        c = store.resolve_alias(i) or (i if store.get_frame(i) is not None else None)
        if c and c not in canonicals:
            canonicals.append(c)

    if not canonicals:
        canonical = candidates[0]  # new device — its first (highest-priority) id is canonical
        store.upsert_frame(Frame.served(canonical, backend=backend_name))
    else:
        canonical = canonicals[0]
        for other in canonicals[1:]:  # the same device under different ids -> merge into one
            store.rekey_frame(other, canonical)

    for i in candidates:
        store.link_alias(i, canonical)
    store.touch_frame(canonical)
    frame = store.get_frame(canonical)
    assert frame is not None  # just registered/looked up
    return frame


def resolve_or_register_served_frame(store: Store, backend_name: str, frame_code: str) -> Frame:
    """Map a single identified frame-code to a registered `Frame` (see ``resolve_served_frame``)."""
    return resolve_served_frame(store, backend_name, frame_code)


def mount_served_backends(app: FastAPI, backends: list[ServedFrameBackend]) -> None:
    """Include each served backend's router so its frames can poll the manager."""
    for backend in backends:
        app.include_router(backend.router())

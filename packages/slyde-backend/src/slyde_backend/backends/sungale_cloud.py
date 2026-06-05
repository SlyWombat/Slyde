"""Sungale cloud backend — white-label Sungale ePaper/WiFi frames (e.g. Aluratek eFrame).

These frames are NOT LAN devices: they poll a vendor cloud over plain HTTP and pull images. We
*impersonate that cloud* (via an AdGuard Home DNS rewrite of the cloud host to this server) and feed
images from Immich, one-way/read-only — the same principle as the LAN backend, but at the cloud
layer. The frame downloads already-prepared images (e-ink palette + dither, #19) from the cache.

The cloud contract was recovered by static analysis of the eFrame app (see
``experiments/aluratek-eframe/FINDINGS.md``): host ``us.xiaowooya.eframe.sungale.com.cn:8080``,
plain HTTP, bearer auth, base ``/xiaowooya/api/v1``. This backend implements the recovered endpoint
*surface* and the full delivery flow (list -> per-image URL -> download from cache).

NOTE: the exact JSON envelope field names are a best effort pending the live frame->cloud capture
(#9). The structure (endpoints, identification, the list->file download flow) is what matters; field
names are easy to adjust once a real wake is captured.

Frame family (#14): the backend is host-agnostic — it answers whatever the frame polls, so the same
code serves any regional Sungale rebrand (``us.``/``eu.``/… prefixes) once that host is rewritten
here. ``CLOUD_HOST`` is the documented default DNS-rewrite target.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from memento_core import FrameInfo, Ports

from ..frame import Frame
from ..serving import resolve_or_register_served_frame
from .base import FrameCapabilities, ServedFrameBackend

# Recovered cloud contract (see experiments/aluratek-eframe/FINDINGS.md).
CLOUD_HOST = "us.xiaowooya.eframe.sungale.com.cn"
CLOUD_PORT = 8080
API_BASE = "/xiaowooya/api/v1"


def _ok(data: Any) -> dict[str, Any]:
    """The vendor's success envelope (best-effort; confirm with the live capture #9)."""
    return {"code": 0, "msg": "ok", "data": data}


def _media_type(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


class SungaleCloudBackend(ServedFrameBackend):
    name = "sungale-cloud"
    capabilities = FrameCapabilities(
        interaction="served",  # the frame polls a server we run; we never connect to it
        transport="cloud",
        color_model="epaper",  # Spectra-6 e-ink: needs palette + dither (see processing.py)
        discovery=False,  # cloud frames register by frame-code, not LAN broadcast
        albums=False,
        thumbnails=False,
        upload=True,  # photos are pushed to the (impersonated) cloud
        delete=True,
        ota=False,  # firmware/OTA path not yet characterized (see #12)
    )

    def discover(self, *, timeout: float = 4.0, ports: Ports | None = None) -> list[FrameInfo]:
        # Cloud frames are not discoverable on the LAN; they reach out to the cloud themselves.
        return []

    def identify(self, request: Request) -> str | None:
        """Frame-code the device presents (bearer token / X-Frame-Code / frame_id query)."""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() or None
        return request.headers.get("x-frame-code") or request.query_params.get("frame_id")

    def _frame_from(self, request: Request) -> Frame:
        code = self.identify(request)
        if not code:
            raise HTTPException(status_code=401, detail="frame not identified")
        return resolve_or_register_served_frame(request.app.state.store, self.name, code)

    def router(self) -> APIRouter:
        """The HTTP surface the frame polls, mounted at the cloud's path (``/xiaowooya/api/v1``)."""
        router = APIRouter(prefix=API_BASE, tags=["sungale-frame"])

        @router.api_route("/user/login", methods=["POST"])
        async def user_login(request: Request) -> dict[str, Any]:
            # The frame authenticates; we issue a token == its frame-code so later bearer auth maps
            # back to the same frame. The code comes from a header/query or a body identifier.
            code = self.identify(request)
            if not code:
                body = await _json(request)
                for k in ("frame_id", "frameId", "sn", "serial", "device_id", "code", "username"):
                    if body.get(k):
                        code = str(body[k])
                        break
            if not code:
                raise HTTPException(status_code=400, detail="no frame identifier in login")
            frame = resolve_or_register_served_frame(request.app.state.store, self.name, code)
            return _ok({"token": frame.id, "user_id": frame.id, "frame_id": frame.id})

        @router.api_route("/frame/ping", methods=["GET", "POST"])
        async def frame_ping(request: Request) -> dict[str, Any]:
            frame = self._frame_from(request)
            return _ok({"frame_id": frame.id})

        @router.api_route("/frame/list", methods=["GET", "POST"])
        async def frame_list(request: Request) -> dict[str, Any]:
            frame = self._frame_from(request)
            return _ok({"list": [{"id": frame.id, "name": frame.name or frame.id}]})

        @router.api_route("/setting/detail", methods=["GET", "POST"])
        async def setting_detail(request: Request) -> dict[str, Any]:
            self._frame_from(request)
            return _ok({"display_orientation": "landscape", "timing_type": "interval"})

        @router.api_route("/schedule/list", methods=["GET", "POST"])
        async def schedule_list(request: Request) -> dict[str, Any]:
            self._frame_from(request)
            return _ok({"list": []})

        @router.api_route("/image_library/list", methods=["GET", "POST"])
        async def image_library_list(request: Request) -> dict[str, Any]:
            # The photos this frame should show: prepared images already in the cache, each as a URL
            # the frame downloads from the file endpoint below.
            frame = self._frame_from(request)
            cache = request.app.state.image_cache
            base = str(request.base_url).rstrip("/")
            items = [
                {
                    "id": key,
                    "name": key,
                    "url": f"{base}{API_BASE}/image_library/file/{frame.id}/{key}",
                }
                for key in cache.keys(frame.id)
            ]
            return _ok({"list": items, "total": len(items)})

        @router.get("/image_library/file/{frame_id}/{key}")
        async def image_library_file(frame_id: str, key: str, request: Request) -> Response:
            data: bytes | None = request.app.state.image_cache.get(frame_id, key)
            if data is None:
                raise HTTPException(status_code=404, detail="image not found")
            return Response(content=data, media_type=_media_type(data))

        return router

    async def respond(self, frame: Frame, request: Request) -> Response:
        """Serve the frame its current prepared image directly (a convenience / fallback)."""
        data: bytes | None = await request.app.state.frame_delivery.image_for(frame)
        if data is None:
            return Response(status_code=204)
        return Response(content=data, media_type=_media_type(data))


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}

"""Sungale cloud backend — for white-label Sungale ePaper/WiFi frames (e.g. Aluratek eFrame).

These frames are NOT LAN devices: they poll a vendor cloud over plain HTTP and pull images. This
backend's strategy is to *impersonate that cloud* (via a DNS rewrite of the cloud host to a local
server) and feed images from Immich, one-way/read-only — the same principle as the LAN backend, but
at the cloud layer instead of the device layer.

The cloud API was recovered by static analysis of the eFrame app and is documented in
``experiments/aluratek-eframe/FINDINGS.md`` (host ``us.xiaowooya.eframe.sungale.com.cn:8080``, plain
HTTP, bearer auth, ``/xiaowooya/api/v1`` endpoint map). The working implementation is pending the
live frame->cloud capture and the responder build — tracked in GitHub issues #9, #8 and #14. Until
then this backend declares its capabilities but raises ``NotImplementedError`` when opening a
session, so it is registered and selectable but honestly incomplete.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from memento_core import FrameInfo, Ports

from ..frame import Frame
from ..serving import resolve_or_register_served_frame
from .base import FrameCapabilities, ServedFrameBackend

# Recovered cloud contract (see experiments/aluratek-eframe/FINDINGS.md).
CLOUD_HOST = "us.xiaowooya.eframe.sungale.com.cn"
CLOUD_PORT = 8080
API_BASE = "/xiaowooya/api/v1"
# Endpoints the frame is expected to poll (confirm exact set + shapes via the live capture, #9).
FRAME_ENDPOINTS = (
    "frame/ping",
    "image_library/list",
    "setting/detail",
    "schedule/list",
)


class SungaleCloudBackend(ServedFrameBackend):
    name = "sungale-cloud"
    capabilities = FrameCapabilities(
        interaction="served",  # the frame polls a server we run; we never connect to it
        transport="cloud",
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
        """Frame-code the device presents. Exact location is confirmed by the live capture (#9);
        we accept the bearer token, an X-Frame-Code header, or a frame_id query param."""
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
        """The HTTP surface the frame polls (mounted at the cloud's path, ``/xiaowooya/api/v1``).

        NOTE: the exact endpoint set + response shapes are placeholders pending the live capture
        and the Immich-backed responder (#8/#9). This wires the *mechanism* — identify the frame,
        register it, and serve it via the delivery seam — end-to-end.
        """
        router = APIRouter(prefix=API_BASE, tags=["sungale-frame"])

        @router.api_route("/frame/ping", methods=["GET", "POST"])
        async def frame_ping(request: Request) -> dict[str, str]:
            frame = self._frame_from(request)
            return {"status": "ok", "frame": frame.id}

        @router.api_route("/image_library/list", methods=["GET", "POST"])
        async def image_library_list(request: Request) -> Response:
            frame = self._frame_from(request)
            return await self.respond(frame, request)

        return router

    async def respond(self, frame: Frame, request: Request) -> Response:
        """Serve the frame its image via the delivery seam (#8/#19/#23 supply the real pipeline)."""
        data: bytes | None = await request.app.state.frame_delivery.image_for(frame)
        if data is None:
            return Response(status_code=204)
        return Response(content=data, media_type="image/jpeg")

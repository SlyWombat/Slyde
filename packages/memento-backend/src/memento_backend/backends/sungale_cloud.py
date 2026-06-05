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

from typing import TYPE_CHECKING

from memento_core import FrameInfo, Ports

from .base import FrameCapabilities, ServedFrameBackend

if TYPE_CHECKING:
    from fastapi import APIRouter, Request, Response

    from ..frame import Frame

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

_PENDING = (
    "Sungale served backend not implemented yet — pending served-mounting (#22), curation/delivery "
    "(#23), and the live capture/responder (#8, #9). See experiments/aluratek-eframe/."
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

    def router(self) -> APIRouter:
        raise NotImplementedError(_PENDING)

    def identify(self, request: Request) -> str | None:
        raise NotImplementedError(_PENDING)

    def respond(self, frame: Frame, request: Request) -> Response:
        raise NotImplementedError(_PENDING)

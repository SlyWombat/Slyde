"""SwitchBot account service: discover/register AI Art Frames + read live status (#64).

The ``switchbot`` backend is reached by pushing to SwitchBot's cloud by ``deviceId``, so frames are
enumerated from the *account* (``SwitchBotClient.art_frames``) rather than discovered on the LAN.
This service is the onboarding + status seam: it lists the account's frames and registers each into
the shared frame registry (keyed by ``deviceId``), so a SwitchBot frame becomes a first-class
curate-from-Immich target on the unified delivery queue, like every other frame.

Credentials come from ``Settings`` (``SWITCHBOT_TOKEN`` / ``SWITCHBOT_SECRET``); the
``client_factory`` is injectable so tests run against a mocked ``SwitchBotClient``, no real account.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .backends.switchbot import SwitchBotBackend
from .config import Settings
from .frame import Frame
from .store import Store
from .switchbot import (
    ArtFrameStatus,
    HomeAssistantArtFrameClient,
    SwitchBotClient,
    SwitchBotDevice,
)


class SwitchBotTransport(Protocol):
    async def __aenter__(self) -> SwitchBotTransport: ...
    async def __aexit__(self, *exc: object) -> None: ...
    async def art_frames(self) -> list[SwitchBotDevice]: ...
    async def art_frame_status(self, device_id: str) -> ArtFrameStatus: ...
    async def upload_image_bytes(
        self, device_id: str, image: bytes, *, mime: str = "image/jpeg"
    ) -> Any: ...


def switchbot_client_factory(settings: Settings) -> Callable[[], SwitchBotTransport]:
    """A factory that builds a ``SwitchBotClient`` or ``HomeAssistantArtFrameClient`` from
    configured creds (never hardcoded)."""
    if settings.switchbot_delivery_mode == "ha_service":
        return lambda: HomeAssistantArtFrameClient(
            settings.effective_ha_base_url,
            settings.effective_ha_token,
            settings.ha_art_frame_entity_id,
            device_id=settings.switchbot_device_id,
            # The frame runs the slideshow: fill its slots without flipping through each upload.
            show_on_upload=settings.switchbot_slideshow != "frame",
        )
    return lambda: SwitchBotClient(settings.switchbot_token, settings.switchbot_secret)


class SwitchBotService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        client_factory: Callable[[], SwitchBotTransport] | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._client_factory = client_factory or switchbot_client_factory(settings)

    @property
    def configured(self) -> bool:
        """Whether credentials for the selected SwitchBot delivery mode are present."""
        if self._settings.switchbot_delivery_mode == "ha_service":
            return bool(
                self._settings.effective_ha_base_url
                and self._settings.effective_ha_token
                and self._settings.ha_art_frame_entity_id
            )
        return bool(self._settings.switchbot_token and self._settings.switchbot_secret)

    async def discover_frames(self, *, register: bool = True) -> list[Frame]:
        """List the account's AI Art Frames; register each into the shared registry by ``deviceId``.

        Idempotent: an upsert refreshes a known frame (preserving any user-set name) and registers a
        new one as a ``switchbot`` (we-push) frame, so curation/delivery can target it immediately.
        """
        async with self._client_factory() as client:
            devices = await client.art_frames()
        frames: list[Frame] = []
        for device in devices:
            frame = Frame.cloud_push(
                device.device_id, backend=SwitchBotBackend.name, name=device.name
            )
            if register:
                self._store.upsert_frame(frame)
                self._store.touch_frame(frame.id)
                frame = self._store.get_frame(frame.id) or frame
            frames.append(frame)
        return frames

    def seed_configured_frame(self) -> Frame | None:
        """If an explicit ``SWITCHBOT_DEVICE_ID`` (or ``HA_ART_FRAME_ENTITY_ID`` in ``ha_service``
        mode) is configured, ensure it is registered in ``Store`` on startup so the Web UI shows it
        immediately without requiring a manual ``POST /api/frames/switchbot/discover`` call."""
        device_id = self._settings.switchbot_device_id.strip()
        if not device_id and self._settings.switchbot_delivery_mode == "ha_service":
            device_id = self._settings.ha_art_frame_entity_id.strip()
        if not device_id:
            return None
        existing = self._store.get_frame(device_id)
        if existing is not None:
            return existing  # never overwrite a discovered or user-given name on restart
        frame = Frame.cloud_push(
            device_id, backend=SwitchBotBackend.name, name="SwitchBot Art Frame"
        )
        self._store.upsert_frame(frame)
        return self._store.get_frame(frame.id) or frame

    async def status(self, device_id: str) -> ArtFrameStatus:
        """Live battery / display-mode / current-image / firmware for one frame (status view)."""
        async with self._client_factory() as client:
            return await client.art_frame_status(device_id)

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

from .backends.switchbot import SwitchBotBackend
from .config import Settings
from .frame import Frame
from .store import Store
from .switchbot import ArtFrameStatus, SwitchBotClient


def switchbot_client_factory(settings: Settings) -> Callable[[], SwitchBotClient]:
    """A factory that builds a ``SwitchBotClient`` from configured creds (never hardcoded)."""
    return lambda: SwitchBotClient(settings.switchbot_token, settings.switchbot_secret)


class SwitchBotService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        client_factory: Callable[[], SwitchBotClient] | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._client_factory = client_factory or switchbot_client_factory(settings)

    @property
    def configured(self) -> bool:
        """Whether a token + secret are present (delivery/discovery need them)."""
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

    async def status(self, device_id: str) -> ArtFrameStatus:
        """Live battery / display-mode / current-image / firmware for one frame (status view)."""
        async with self._client_factory() as client:
            return await client.art_frame_status(device_id)

"""SwitchBot AI Art Frame backend: account discovery -> registry + queued push delivery (#64)."""

from __future__ import annotations

import asyncio
import io
from datetime import datetime
from pathlib import Path

from PIL import Image

from slyde_backend.config import Settings
from slyde_backend.delivery_service import DeliveryService
from slyde_backend.imagecache import ImageCache
from slyde_backend.library import FrameLibrary, LibraryItem
from slyde_backend.store import Store
from slyde_backend.switchbot import ART_FRAME, ArtFrameStatus, SwitchBotDevice
from slyde_backend.switchbot_service import SwitchBotService

T0 = datetime(2026, 1, 1, 12, 0, 0)


class StubSwitchBot:
    """A stand-in for ``SwitchBotClient`` (async context manager) that records uploads."""

    def __init__(
        self,
        *,
        devices: list[SwitchBotDevice] | None = None,
        status: ArtFrameStatus | None = None,
        uploaded: list[tuple[str, bytes]] | None = None,
    ) -> None:
        self.devices = devices or []
        self._status = status
        self.uploaded = uploaded if uploaded is not None else []

    async def __aenter__(self) -> StubSwitchBot:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def art_frames(self) -> list[SwitchBotDevice]:
        return self.devices

    async def art_frame_status(self, device_id: str) -> ArtFrameStatus:
        assert self._status is not None
        return self._status

    async def upload_image_bytes(
        self, device_id: str, image: bytes, *, mime: str = "image/jpeg"
    ) -> None:
        self.uploaded.append((device_id, image))


class FakeImmich:
    async def __aenter__(self) -> FakeImmich:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def asset_bytes(self, asset_id: str, size: str = "preview") -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (1200, 800), (40, 80, 160)).save(buf, format="JPEG")
        return buf.getvalue()


def test_discover_registers_account_frames_by_device_id(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "d.db"))
    stub = StubSwitchBot(
        devices=[
            SwitchBotDevice("B0E9FEDEF6E2", ART_FRAME, name="Studio", hub_id="H1"),
            SwitchBotDevice("AA11BB22CC33", ART_FRAME, name="Kitchen"),
        ]
    )
    svc = SwitchBotService(Settings(), store, client_factory=lambda: stub)

    frames = asyncio.run(svc.discover_frames())

    assert [f.id for f in frames] == ["B0E9FEDEF6E2", "AA11BB22CC33"]
    registered = store.get_frame("B0E9FEDEF6E2")
    assert registered is not None
    assert registered.backend == "switchbot"
    assert registered.interaction == "connected"  # we push to it (via the vendor cloud)
    assert registered.name == "Studio" and registered.frame_code == "B0E9FEDEF6E2"


def test_service_reports_unconfigured_without_creds(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "d.db"))
    # `configured` reflects the settings token/secret (what real delivery/discovery need). Pass the
    # creds explicitly so the result is deterministic regardless of any ambient env file.
    blank = Settings(switchbot_token="", switchbot_secret="")
    assert not SwitchBotService(blank, store).configured
    assert SwitchBotService(Settings(switchbot_token="t", switchbot_secret="s"), store).configured


def test_status_pulls_live_battery_and_firmware(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "d.db"))
    stub = StubSwitchBot(
        status=ArtFrameStatus("F1", battery=87, display_mode=1, image_url="u", version="V0.0-0.5")
    )
    svc = SwitchBotService(Settings(), store, client_factory=lambda: stub)
    s = asyncio.run(svc.status("F1"))
    assert s.battery == 87 and s.display_mode == 1 and s.version == "V0.0-0.5"


def test_queued_photo_is_prepped_to_480x800_and_pushed(tmp_path: Path) -> None:
    """The whole point of #64: a curated photo for a SwitchBot frame rides the unified delivery
    queue, gets pre-fitted to the panel's native 480x800 portrait, and is pushed inline via
    ``upload_image_bytes`` keyed by the frame's deviceId."""
    store = Store(str(tmp_path / "d.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    uploaded: list[tuple[str, bytes]] = []
    stub = StubSwitchBot(uploaded=uploaded)
    ds = DeliveryService(
        store,
        library,
        cache,
        frame_service=None,  # never used: the switchbot path doesn't push over the LAN
        immich_factory=FakeImmich,
        settings=Settings(),
        switchbot_factory=lambda: stub,
    )

    # Register the account frame and curate one Immich photo to it.
    svc = SwitchBotService(Settings(), store, client_factory=lambda: stub)
    stub.devices = [SwitchBotDevice("B0E9FEDEF6E2", ART_FRAME, name="Studio")]
    asyncio.run(svc.discover_frames())
    library.set_desired("B0E9FEDEF6E2", [LibraryItem("a1", "one.jpg")])

    assert ds.enqueue_desired("B0E9FEDEF6E2", now=T0) == 1
    counts = asyncio.run(ds.reconcile(now=T0))

    assert counts == {"delivered": 1, "retried": 0, "failed": 0}
    assert len(uploaded) == 1
    device_id, jpeg = uploaded[0]
    assert device_id == "B0E9FEDEF6E2"  # pushed to the right frame by deviceId
    assert jpeg[:3] == b"\xff\xd8\xff"  # a JPEG (the cloud renders it onto the Spectra-6 panel)
    with Image.open(io.BytesIO(jpeg)) as img:
        assert img.size == (480, 800)  # pre-fitted to the panel's portrait canvas
    # The prepared image is also cached, so a retry/preview reuses it (no re-fetch/re-process).
    assert cache.get("B0E9FEDEF6E2", "one.jpg") is not None


def test_switchbot_push_failure_is_transient_and_retried(tmp_path: Path) -> None:
    from slyde_backend.frame import Frame
    from slyde_backend.switchbot import SwitchBotError

    class BoomSwitchBot(StubSwitchBot):
        async def upload_image_bytes(self, device_id, image, *, mime="image/jpeg"):  # type: ignore[no-untyped-def]
            raise SwitchBotError("cloud unreachable")

    store = Store(str(tmp_path / "d.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    ds = DeliveryService(
        store,
        library,
        cache,
        frame_service=None,  # unused on the switchbot push path
        immich_factory=FakeImmich,
        settings=Settings(),
        switchbot_factory=BoomSwitchBot,
    )
    store.upsert_frame(Frame.cloud_push("F-OFF", backend="switchbot", name="Den"))
    library.set_desired("F-OFF", [LibraryItem("a1", "one.jpg")])
    ds.enqueue_desired("F-OFF", now=T0)

    # A cloud/API failure is transient: the photo is rescheduled (retried), never abandoned.
    assert asyncio.run(ds.reconcile(now=T0)) == {"delivered": 0, "retried": 1, "failed": 0}
    assert store.list_deliveries("F-OFF")[0].state == "pending"

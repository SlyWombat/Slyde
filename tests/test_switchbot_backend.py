"""SwitchBot AI Art Frame backend: account discovery -> registry + queued push delivery (#64)."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from PIL import Image

from slyde_backend.config import Settings
from slyde_backend.delivery_service import DeliveryService
from slyde_backend.imagecache import ImageCache
from slyde_backend.library import FrameLibrary, LibraryItem
from slyde_backend.store import Store
from slyde_backend.switchbot import (
    ART_FRAME,
    ArtFrameStatus,
    HomeAssistantArtFrameClient,
    SwitchBotDevice,
)
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


class FakeHAWebSocket:
    """Plays Home Assistant's side of the WebSocket API: auth handshake, then answers each
    ``call_service`` with ``result`` (default: success), recording the calls."""

    def __init__(
        self,
        calls: list[dict[str, object]] | None = None,
        *,
        result: dict[str, object] | None = None,
        hang: bool = False,
    ) -> None:
        self.calls = calls if calls is not None else []
        self.result = result or {"success": True, "result": {"response": {}}}
        self.hang = hang
        self.urls: list[str] = []
        self.tokens: list[str] = []
        self._outbox: list[dict[str, object]] = []

    @contextlib.asynccontextmanager
    async def connect(self, url: str) -> AsyncIterator[FakeHAWebSocket]:
        self.urls.append(url)
        self._outbox = [{"type": "auth_required"}]
        yield self

    async def send(self, message: str) -> None:
        msg = json.loads(message)
        if msg["type"] == "auth":
            self.tokens.append(msg["access_token"])
            self._outbox.append({"type": "auth_ok"})
        elif msg["type"] == "call_service":
            self.calls.append(msg)
            self._outbox.append({"type": "event", "event": {}})  # noise the client must skip
            if not self.hang:  # hang = HA never answers (the action is stuck)
                self._outbox.append({"id": msg["id"], "type": "result", **self.result})

    async def recv(self) -> str:
        if not self._outbox:
            await asyncio.sleep(10)
        return json.dumps(self._outbox.pop(0))


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


def test_13_3_inch_portrait_and_landscape_canvas_and_adaptive_jpeg_cap() -> None:
    """The 13.3" Gen-1 SwitchBot AI Art Frame has a 1200x1600 Spectra-6 panel, and SwitchBot's
    upload endpoint rejects base64 bodies over ~900k chars (~650 KB JPEG). Verify both canvas
    sizing and adaptive quality step-down."""
    from dataclasses import replace

    from slyde_backend.backends.switchbot import switchbot_canvas
    from slyde_backend.frame import Frame
    from slyde_backend.processing import prepare, profile_for

    assert switchbot_canvas("13.3", "portrait") == (1200, 1600)
    assert switchbot_canvas("13.3", "landscape") == (1600, 1200)
    assert switchbot_canvas("31.5", "portrait") == (1440, 2560)

    frame = Frame.cloud_push("SB13", backend="switchbot", name="Living Room 13.3")
    settings = Settings(
        switchbot_frame_size="13.3",
        switchbot_orientation="portrait",
        switchbot_max_image_bytes=450_000,
    )
    profile = profile_for(frame, settings, canvas=(3240, 2160))
    assert profile.canvas == (1200, 1600)
    assert profile.max_bytes == 450_000

    # Generate a noisy 1200x1600 image that exceeds 650 KB at quality=90, and verify prepare()
    # steps down JPEG quality so the output stays under max_bytes (450 KB).
    img = Image.effect_noise((1200, 1600), 64.0).convert("RGB")
    raw = io.BytesIO()
    img.save(raw, format="PNG")
    uncapped = prepare(raw.getvalue(), replace(profile, max_bytes=0))
    assert len(uncapped) > 650_000
    out = prepare(raw.getvalue(), profile)
    assert len(out) <= 450_000
    with Image.open(io.BytesIO(out)) as decoded:
        assert decoded.size == (1200, 1600)


def test_shuffle_deck_10_slot_rotation_cycles_without_replacement(tmp_path: Path) -> None:
    """With switchbot_slideshow='frame' (10 slots) on a 25-photo Immich album, FolderSyncService
    draws 10 random photos per rotation without repeating any photo until all 25 have been shown
    once."""
    import random
    from datetime import UTC, timedelta

    from slyde_backend.folder_sync import FolderSyncService
    from slyde_backend.frame import Frame
    from slyde_backend.immich import ImmichAsset

    class Album25Immich:
        async def __aenter__(self) -> Album25Immich:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def album_assets(self, album_id: str) -> list[ImmichAsset]:
            return [ImmichAsset(id=f"p{i}", file_name=f"p{i}.jpg", type="IMAGE") for i in range(25)]

        async def asset_bytes(self, asset_id: str, size: str = "preview") -> bytes:
            buf = io.BytesIO()
            Image.new("RGB", (120, 160), (50, 100, 150)).save(buf, format="JPEG")
            return buf.getvalue()

    store = Store(str(tmp_path / "shuffle.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    uploaded: list[tuple[str, bytes]] = []
    stub = StubSwitchBot(uploaded=uploaded)
    settings = Settings(
        switchbot_frame_size="13.3",
        switchbot_slideshow="frame",
        switchbot_slideshow_interval_minutes=60,
    )
    ds = DeliveryService(
        store,
        library,
        cache,
        frame_service=None,
        immich_factory=Album25Immich,  # type: ignore[arg-type]
        settings=settings,
        switchbot_factory=lambda: stub,
    )
    sync = FolderSyncService(
        settings,
        store,
        library,
        ds,
        Album25Immich,  # type: ignore[arg-type]
    )
    store.upsert_frame(Frame.cloud_push("SB13", backend="switchbot", name="13.3 Frame"))
    rng = random.Random(42)
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    # Batch 1 at t0: draws 10 unique photos from the 25-photo album.
    r1 = asyncio.run(sync.reconcile("SB13", "alb-1", "Family", now=t0, rng=rng))
    assert r1.total == 10 and r1.uploaded == 10
    asyncio.run(ds.drain(now=t0))
    batch1 = [i.asset_id for i in library.desired("SB13")]
    assert len(batch1) == 10 and len(set(batch1)) == 10

    # 30 minutes later (< 60m rotation interval): reconcile keeps the same 10 photos on the frame.
    t_early = t0 + timedelta(minutes=30)
    r_hold = asyncio.run(sync.reconcile("SB13", "alb-1", "Family", now=t_early, rng=rng))
    assert [i.asset_id for i in library.desired("SB13")] == batch1
    assert r_hold.uploaded == 0 and r_hold.removed == 0

    # Batch 2 at t0 + 61m: draws 10 NEW unseen photos (0 overlap with batch 1).
    t1 = t0 + timedelta(minutes=61)
    r2 = asyncio.run(sync.reconcile("SB13", "alb-1", "Family", now=t1, rng=rng))
    batch2 = [i.asset_id for i in library.desired("SB13")]
    assert len(batch2) == 10 and len(set(batch2)) == 10
    assert set(batch1).isdisjoint(set(batch2))
    assert r2.uploaded == 10 and r2.removed == 10
    asyncio.run(ds.drain(now=t1))

    # Batch 3 at t1 + 61m: only 5 unseen photos remain in Epoch 1 -> takes all 5, wraps to Epoch 2,
    # and fills the remaining 5 slots from the reset pool without duplicating inside Batch 3.
    t2 = t1 + timedelta(minutes=61)
    asyncio.run(sync.reconcile("SB13", "alb-1", "Family", now=t2, rng=rng))
    batch3 = [i.asset_id for i in library.desired("SB13")]
    assert len(batch3) == 10 and len(set(batch3)) == 10
    # Across the 3 batches of 10, all 25 photos of the Immich album have been shown!
    assert set(batch1) | set(batch2) | set(batch3) == {f"p{i}" for i in range(25)}
    # And photos from batch 1 that re-entered in batch 3 were re-queued & delivered (not skipped).
    assert asyncio.run(ds.drain(now=t2))["delivered"] >= 5


def test_home_assistant_bridge_discovers_and_delivers_via_show_image(
    tmp_path: Path, monkeypatch: object
) -> None:
    """In ha_service mode (Option A / HA Add-on), Slyde discovers the frame from Home Assistant's
    local_images sensor and delivers prepared 1200x1600 JPEGs via switchbot_art_frame.show_image."""
    import json as _json

    import httpx

    from slyde_backend.config import load_ha_addon_options
    from slyde_backend.switchbot import HomeAssistantArtFrameClient

    calls: list[dict[str, object]] = []

    def ha_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer SUP-TOK-123"
        if request.url.path.endswith("/api/states/sensor.art_frame_eeff_local_images"):
            return httpx.Response(
                200,
                json={
                    "entity_id": "sensor.art_frame_eeff_local_images",
                    "state": "10",
                    "attributes": {
                        "friendly_name": "Hallway Art Frame Local images",
                        "image_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                        "current_image_id": 10,
                        "free_slots": 0,
                    },
                },
            )
        return httpx.Response(404)

    ws = FakeHAWebSocket(calls)

    # Verify HA Add-on options.json loader and SUPERVISOR_TOKEN fallback.
    opts = tmp_path / "options.json"
    opts.write_text(
        _json.dumps(
            {
                "switchbot_delivery_mode": "ha_service",
                "ha_art_frame_entity_id": "sensor.art_frame_eeff_local_images",
                "switchbot_frame_size": "13.3",
            }
        )
    )
    monkeypatch.setenv("SUPERVISOR_TOKEN", "SUP-TOK-123")  # type: ignore[attr-defined]
    monkeypatch.delenv("SWITCHBOT_DELIVERY_MODE", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("HA_ART_FRAME_ENTITY_ID", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("SWITCHBOT_FRAME_SIZE", raising=False)  # type: ignore[attr-defined]
    load_ha_addon_options(opts)
    loaded = Settings()
    assert loaded.switchbot_delivery_mode == "ha_service"
    assert loaded.ha_art_frame_entity_id == "sensor.art_frame_eeff_local_images"
    assert loaded.switchbot_frame_size == "13.3"
    assert loaded.effective_ha_base_url == "http://supervisor/core"
    assert loaded.effective_ha_token == "SUP-TOK-123"

    settings = Settings(
        switchbot_delivery_mode="ha_service",
        ha_art_frame_entity_id="sensor.art_frame_eeff_local_images",
        switchbot_frame_size="13.3",
        switchbot_orientation="portrait",
    )
    assert settings.effective_ha_base_url == "http://supervisor/core"
    assert settings.effective_ha_token == "SUP-TOK-123"

    def ha_factory() -> HomeAssistantArtFrameClient:
        return HomeAssistantArtFrameClient(
            settings.effective_ha_base_url,
            settings.effective_ha_token,
            settings.ha_art_frame_entity_id,
            transport=httpx.MockTransport(ha_handler),
            websocket_connect=ws.connect,
        )

    store = Store(str(tmp_path / "ha.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    svc = SwitchBotService(settings, store, client_factory=ha_factory)
    assert svc.configured

    frames = asyncio.run(svc.discover_frames())
    assert len(frames) == 1
    assert frames[0].id == "sensor.art_frame_eeff_local_images"
    assert frames[0].name == "Hallway Art Frame"

    ds = DeliveryService(
        store,
        library,
        cache,
        frame_service=None,
        immich_factory=FakeImmich,
        settings=settings,
        switchbot_factory=ha_factory,
    )
    library.set_desired(frames[0].id, [LibraryItem("a1", "one.jpg")])
    assert ds.enqueue_desired(frames[0].id, now=T0) == 1
    counts = asyncio.run(ds.reconcile(now=T0))
    assert counts == {"delivered": 1, "retried": 0, "failed": 0}
    # The action ran over the Supervisor's WebSocket proxy, authenticated with SUPERVISOR_TOKEN.
    assert ws.urls == ["ws://supervisor/core/websocket"]
    assert ws.tokens == ["SUP-TOK-123"]
    assert len(calls) == 1
    call = calls[0]
    assert (call["domain"], call["service"]) == ("switchbot_art_frame", "show_image")
    assert call["target"] == {"entity_id": "sensor.art_frame_eeff_local_images"}
    assert call["return_response"] is True
    data = call["service_data"]
    assert isinstance(data, dict)
    assert data["make_room"] is True
    assert str(data["image_data"]).startswith("data:image/jpeg;base64,")


def _ha_client(ws: FakeHAWebSocket, **kw: object) -> HomeAssistantArtFrameClient:
    return HomeAssistantArtFrameClient(
        "http://ha.local:8123",
        "TOK",
        "sensor.art_frame_local_images",
        websocket_connect=ws.connect,
        **kw,  # type: ignore[arg-type]
    )


def _ha_delivery(tmp_path: Path, ws: FakeHAWebSocket) -> tuple[Store, DeliveryService]:
    from slyde_backend.frame import Frame

    store = Store(str(tmp_path / "d.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    ds = DeliveryService(
        store,
        library,
        cache,
        frame_service=None,
        immich_factory=FakeImmich,
        settings=Settings(),
        switchbot_factory=lambda: _ha_client(ws),
    )
    store.upsert_frame(Frame.cloud_push("sensor.art_frame_local_images", backend="switchbot"))
    library.set_desired("sensor.art_frame_local_images", [LibraryItem("a1", "one.jpg")])
    ds.enqueue_desired("sensor.art_frame_local_images", now=T0)
    return store, ds


def test_ha_websocket_url_for_home_assistant_and_supervisor() -> None:
    from slyde_backend.switchbot import ha_websocket_url

    assert ha_websocket_url("http://ha.local:8123") == "ws://ha.local:8123/api/websocket"
    assert ha_websocket_url("https://ha.example.com/") == "wss://ha.example.com/api/websocket"
    assert ha_websocket_url("http://supervisor/core") == "ws://supervisor/core/websocket"


def test_ha_bridge_rejects_a_non_sensor_entity_id() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValueError, match="not a sensor entity id"):
        HomeAssistantArtFrameClient("http://ha.local:8123", "TOK", "../services/x")
    with pytest.raises(ValidationError):
        Settings(ha_art_frame_entity_id="switch.not_a_frame")
    assert Settings(ha_art_frame_entity_id="").ha_art_frame_entity_id == ""


def test_ha_validation_error_fails_the_delivery_permanently(tmp_path: Path) -> None:
    """HA refusing the image (ServiceValidationError) won't change on retry: fail, don't loop."""
    ws = FakeHAWebSocket(
        result={
            "success": False,
            "error": {"code": "service_validation_error", "message": "image too large"},
        }
    )
    store, ds = _ha_delivery(tmp_path, ws)
    assert asyncio.run(ds.reconcile(now=T0)) == {"delivered": 0, "retried": 0, "failed": 1}
    row = store.list_deliveries("sensor.art_frame_local_images")[0]
    assert row.state == "failed" and "image too large" in (row.last_error or "")


def test_ha_action_error_is_retried(tmp_path: Path) -> None:
    """A HomeAssistantError (frame asleep, BLE/cloud hiccup) is reported — not swallowed as a
    success like the 10 s REST endpoint would — and the photo is retried."""
    ws = FakeHAWebSocket(
        result={
            "success": False,
            "error": {"code": "home_assistant_error", "message": "never appeared in album"},
        }
    )
    store, ds = _ha_delivery(tmp_path, ws)
    assert asyncio.run(ds.reconcile(now=T0)) == {"delivered": 0, "retried": 1, "failed": 0}
    assert store.list_deliveries("sensor.art_frame_local_images")[0].state == "pending"


def test_ha_action_that_never_answers_times_out_as_transient() -> None:
    import pytest

    from slyde_backend.switchbot import SwitchBotError, SwitchBotRejectedError

    async def run() -> None:
        async with _ha_client(FakeHAWebSocket(hang=True), timeout=0.05) as client:
            await client.upload_image_bytes("x", b"\xff\xd8\xff")

    with pytest.raises(SwitchBotError, match="no answer") as exc:
        asyncio.run(run())
    assert not isinstance(exc.value, SwitchBotRejectedError)


def test_ha_status_reports_unknown_battery_instead_of_guessing() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"state": "7", "attributes": {"current_image_id": 4}})

    async def run() -> ArtFrameStatus:
        async with _ha_client(FakeHAWebSocket(), transport=httpx.MockTransport(handler)) as client:
            return await client.art_frame_status("sensor.art_frame_local_images")

    s = asyncio.run(run())
    assert s.battery is None and s.display_mode is None and s.version == ""
    assert s.image_url == "slot:4"


def test_seeding_keeps_the_known_frame_name(tmp_path: Path) -> None:
    from slyde_backend.frame import Frame

    store = Store(str(tmp_path / "d.db"))
    store.upsert_frame(Frame.cloud_push("B0E9FEDEF6E2", backend="switchbot", name="Studio"))
    svc = SwitchBotService(Settings(switchbot_device_id="B0E9FEDEF6E2"), store)
    seeded = svc.seed_configured_frame()
    assert seeded is not None and seeded.name == "Studio"
    fresh = SwitchBotService(
        Settings(switchbot_device_id="NEWFRAME"), store
    ).seed_configured_frame()
    assert fresh is not None and fresh.name == "SwitchBot Art Frame"


def test_cached_image_for_another_panel_size_is_prepared_again(tmp_path: Path) -> None:
    """Switching SWITCHBOT_FRAME_SIZE must not keep pushing images fitted to the old panel."""
    from slyde_backend.frame import Frame

    store = Store(str(tmp_path / "d.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    uploaded: list[tuple[str, bytes]] = []
    stub = StubSwitchBot(uploaded=uploaded)
    store.upsert_frame(Frame.cloud_push("SB", backend="switchbot"))
    library.set_desired("SB", [LibraryItem("a1", "one.jpg")])
    old = io.BytesIO()
    Image.new("RGB", (480, 800)).save(old, format="JPEG")
    cache.put("SB", "one.jpg", old.getvalue())  # left over from the 7.3" setting

    ds = DeliveryService(
        store,
        library,
        cache,
        frame_service=None,
        immich_factory=FakeImmich,
        settings=Settings(switchbot_frame_size="13.3"),
        switchbot_factory=lambda: stub,
    )
    ds.enqueue_desired("SB", now=T0)
    asyncio.run(ds.reconcile(now=T0))
    with Image.open(io.BytesIO(uploaded[0][1])) as img:
        assert img.size == (1200, 1600)


def test_shuffle_new_epoch_avoids_the_batch_on_the_frame(tmp_path: Path) -> None:
    """When an epoch runs out exactly, the next draw starts a fresh epoch — but it must not re-pick
    the photos it is replacing while other photos are available."""
    import random

    store = Store(str(tmp_path / "d.db"))
    ids = [f"p{i}" for i in range(20)]
    rng = random.Random(7)
    b1 = store.draw_shuffle_batch("F", "Fam", ids, 10, rng=rng)
    b2 = store.draw_shuffle_batch("F", "Fam", ids, 10, rng=rng, avoid=b1)  # exhausts epoch 1
    assert set(b1).isdisjoint(b2)
    for _ in range(20):  # epoch 2 draws: never the batch it replaces
        b3 = store.draw_shuffle_batch("F", "Fam", ids, 10, rng=rng, avoid=b2)
        assert set(b3).isdisjoint(b2)
        store.forget_shuffle_draws("F", "Fam", b3)


def test_shuffle_top_up_keeps_rotation_clock_and_undelivered_photos_return(tmp_path: Path) -> None:
    import random
    from datetime import UTC, timedelta

    from slyde_backend.folder_sync import FolderSyncService
    from slyde_backend.frame import Frame
    from slyde_backend.immich import ImmichAsset

    album = [f"p{i}" for i in range(30)]

    class Immich:
        async def __aenter__(self) -> Immich:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def album_assets(self, album_id: str) -> list[ImmichAsset]:
            return [ImmichAsset(id=a, file_name=f"{a}.jpg", type="IMAGE") for a in album]

    store = Store(str(tmp_path / "d.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    settings = Settings(switchbot_slideshow="frame", switchbot_slideshow_interval_minutes=60)
    ds = DeliveryService(
        store,
        library,
        cache,
        frame_service=None,
        immich_factory=FakeImmich,
        settings=settings,
        switchbot_factory=lambda: StubSwitchBot(),
    )
    sync = FolderSyncService(settings, store, library, ds, Immich)  # type: ignore[arg-type]
    store.upsert_frame(Frame.cloud_push("SB", backend="switchbot"))
    rng = random.Random(3)
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    asyncio.run(sync.reconcile("SB", "alb", "Fam", now=t0, rng=rng))
    batch1 = [i.asset_id for i in library.desired("SB")]
    _, rotated_at = store.get_shuffle_epoch("SB", "Fam")

    # Two photos of the held batch are deleted from the album: their slots are refilled now,
    # without restarting the rotation interval.
    for gone in batch1[:2]:
        album.remove(gone)
    asyncio.run(sync.reconcile("SB", "alb", "Fam", now=t0 + timedelta(minutes=10), rng=rng))
    held = [i.asset_id for i in library.desired("SB")]
    assert len(held) == 10 and set(batch1[2:]) <= set(held)
    assert store.get_shuffle_epoch("SB", "Fam")[1] == rotated_at

    # Nothing was delivered: at rotation the 10 held photos never reached the frame, so they go
    # back into the deck (unseen) instead of counting as shown, and the new batch avoids them.
    asyncio.run(sync.reconcile("SB", "alb", "Fam", now=t0 + timedelta(minutes=61), rng=rng))
    batch2 = {i.asset_id for i in library.desired("SB")}
    assert len(batch2) == 10 and batch2.isdisjoint(held)
    with sqlite3.connect(tmp_path / "d.db") as conn:
        seen = {r[0] for r in conn.execute("SELECT asset_id FROM shuffle_history")}
    assert seen.isdisjoint(held)


def test_slyde_slideshow_sends_one_new_photo_per_interval(tmp_path: Path) -> None:
    """'slyde' mode: one photo per change, no repeats before the album is through, and the sync
    cycle runs on the slideshow clock."""
    import random
    from datetime import UTC, timedelta

    from slyde_backend.folder_sync import FolderSyncService
    from slyde_backend.frame import Frame
    from slyde_backend.immich import ImmichAsset

    class Immich:
        async def __aenter__(self) -> Immich:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def album_assets(self, album_id: str) -> list[ImmichAsset]:
            return [ImmichAsset(id=f"p{i}", file_name=f"p{i}.jpg", type="IMAGE") for i in range(4)]

    settings = Settings(
        switchbot_slideshow="slyde",
        switchbot_slideshow_interval_minutes=30,
        sync_interval_minutes=5,
    )
    assert settings.shuffle_batch_size == 1
    assert settings.effective_sync_interval_minutes == 30
    assert Settings(switchbot_slideshow="frame").shuffle_batch_size == 10
    assert Settings().shuffle_batch_size == 0  # off: whole album, as before

    store = Store(str(tmp_path / "d.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    ds = DeliveryService(
        store,
        library,
        cache,
        frame_service=None,
        immich_factory=FakeImmich,
        settings=settings,
        switchbot_factory=lambda: StubSwitchBot(),
    )
    sync = FolderSyncService(settings, store, library, ds, Immich)  # type: ignore[arg-type]
    store.upsert_frame(Frame.cloud_push("SB", backend="switchbot"))
    rng = random.Random(1)
    t = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    shown = []
    for step in range(4):
        asyncio.run(
            sync.reconcile("SB", "alb", "Fam", now=t + timedelta(minutes=30 * step), rng=rng)
        )
        asyncio.run(ds.drain(now=t + timedelta(minutes=30 * step)))
        (item,) = library.desired("SB")
        shown.append(item.asset_id)
        # Halfway to the next change nothing moves.
        asyncio.run(
            sync.reconcile("SB", "alb", "Fam", now=t + timedelta(minutes=30 * step + 15), rng=rng)
        )
        assert [i.asset_id for i in library.desired("SB")] == [item.asset_id]
    assert sorted(shown) == ["p0", "p1", "p2", "p3"]  # each photo once
    assert store.get_frame("SB").last_seen is not None  # type: ignore[union-attr]


def test_quiet_hours_window_wraps_midnight() -> None:
    from datetime import UTC

    from slyde_backend.folder_sync import in_quiet_hours

    def at(h: int, m: int = 0) -> datetime:
        # in_quiet_hours compares in the host's local time; build the instant from local time.
        return datetime(2026, 6, 1, h, m).astimezone(UTC)

    assert in_quiet_hours("22:00-07:00", at(23)) and in_quiet_hours("22:00-07:00", at(6, 59))
    assert not in_quiet_hours("22:00-07:00", at(7)) and not in_quiet_hours("22:00-07:00", at(12))
    assert in_quiet_hours("13:00-14:00", at(13, 30)) and not in_quiet_hours("13:00-14:00", at(14))
    assert not in_quiet_hours("", at(3))


def _slyde_sync(tmp_path: Path, settings: Settings):  # type: ignore[no-untyped-def]
    from slyde_backend.folder_sync import FolderSyncService
    from slyde_backend.frame import Frame
    from slyde_backend.immich import ImmichAsset

    class Immich:
        async def __aenter__(self) -> Immich:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def album_assets(self, album_id: str) -> list[ImmichAsset]:
            return [ImmichAsset(id=f"p{i}", file_name=f"p{i}.jpg", type="IMAGE") for i in range(5)]

    store = Store(str(tmp_path / "d.db"))
    cache = ImageCache(str(tmp_path / "cache"))
    library = FrameLibrary(store, cache)
    ds = DeliveryService(
        store,
        library,
        cache,
        frame_service=None,
        immich_factory=FakeImmich,
        settings=settings,
        switchbot_factory=lambda: StubSwitchBot(),
    )
    sync = FolderSyncService(settings, store, library, ds, Immich)  # type: ignore[arg-type]
    store.upsert_frame(Frame.cloud_push("SB", backend="switchbot"))
    return store, library, sync


def test_quiet_hours_hold_the_photo_but_next_photo_still_advances(tmp_path: Path) -> None:
    from datetime import UTC, timedelta

    settings = Settings(
        switchbot_slideshow="slyde",
        switchbot_slideshow_interval_minutes=30,
        switchbot_quiet_hours="00:00-23:59",  # always quiet
    )
    _store, library, sync = _slyde_sync(tmp_path, settings)
    t = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    asyncio.run(sync.bind("SB", "alb", "Fam"))  # the first photo always goes out
    first = [i.asset_id for i in library.desired("SB")]
    assert len(first) == 1

    asyncio.run(sync.reconcile("SB", "alb", "Fam", now=t + timedelta(hours=5)))
    assert [i.asset_id for i in library.desired("SB")] == first  # quiet: held

    assert asyncio.run(sync.advance("SB")) == 1  # "next photo now" ignores quiet hours
    second = [i.asset_id for i in library.desired("SB")]
    assert len(second) == 1 and second != first


def test_ha_bridge_reads_battery_from_the_device_and_stores_without_showing() -> None:
    import httpx

    templates: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/template":
            templates.append(json.loads(request.content)["template"])
            return httpx.Response(200, text="76")
        return httpx.Response(200, json={"state": "3", "attributes": {"current_image_id": 38}})

    ws = FakeHAWebSocket()

    async def run() -> ArtFrameStatus:
        async with _ha_client(
            ws, transport=httpx.MockTransport(handler), show_on_upload=False
        ) as client:
            await client.upload_image_bytes("x", b"\xff\xd8\xff")
            return await client.art_frame_status("x")

    status = asyncio.run(run())
    assert status.battery == 76
    assert "device_entities(device_id('sensor.art_frame_local_images'))" in templates[0]
    data = ws.calls[0]["service_data"]
    assert isinstance(data, dict) and data["show"] is False


def test_ha_bridge_battery_unknown_when_template_fails() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/template":
            return httpx.Response(401)
        return httpx.Response(200, json={"state": "3", "attributes": {}})

    async def run() -> ArtFrameStatus:
        async with _ha_client(FakeHAWebSocket(), transport=httpx.MockTransport(handler)) as c:
            return await c.art_frame_status("x")

    assert asyncio.run(run()).battery is None


def test_ha_bridge_shows_on_upload_by_default_without_sending_the_option() -> None:
    ws = FakeHAWebSocket()

    async def run() -> None:
        async with _ha_client(ws) as client:
            await client.upload_image_bytes("x", b"\xff\xd8\xff")

    asyncio.run(run())
    data = ws.calls[0]["service_data"]
    assert isinstance(data, dict) and "show" not in data  # older integrations keep working

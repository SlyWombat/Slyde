"""Slyde's slideshow for SwitchBot frames: the whole library is the deck, only a batch is on air."""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from slyde_backend.config import Settings
from slyde_backend.delivery_service import DeliveryService
from slyde_backend.folder_sync import FolderSyncService
from slyde_backend.frame import Frame
from slyde_backend.imagecache import ImageCache
from slyde_backend.immich import ImmichAsset
from slyde_backend.library import FrameLibrary, LibraryItem
from slyde_backend.presence import Presence, is_present
from slyde_backend.slideshow import DECK, Slideshow, SlideshowConfig
from slyde_backend.store import Store
from test_switchbot_backend import FakeImmich, StubSwitchBot

T = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class Harness:
    def __init__(self, tmp_path: Path, settings: Settings, *, seed: int = 1) -> None:
        self.store = Store(str(tmp_path / "d.db"))
        self.library = FrameLibrary(self.store, ImageCache(str(tmp_path / "cache")))
        self.uploaded: list[tuple[str, bytes]] = []
        stub = StubSwitchBot(uploaded=self.uploaded)
        self.ds = DeliveryService(
            self.store,
            self.library,
            ImageCache(str(tmp_path / "cache")),
            frame_service=None,  # type: ignore[arg-type]
            immich_factory=FakeImmich,  # type: ignore[arg-type]
            settings=settings,
            switchbot_factory=lambda: stub,
            slideshow=Slideshow(settings, self.store, self.library, rng=random.Random(seed)),
        )
        self.store.upsert_frame(Frame.cloud_push("SB", backend="switchbot"))

    def curate(self, ids: list[str]) -> None:
        self.library.set_desired("SB", [LibraryItem(a, f"{a}.jpg") for a in ids])

    def step(self, now: datetime, *, advance: bool = False) -> int:
        queued = self.ds.enqueue_desired("SB", now=now, advance=advance)
        asyncio.run(self.ds.drain(now=now))
        return queued

    def on_air(self) -> list[str]:
        return self.store.slideshow_batch("SB")


def test_curating_an_album_sends_one_photo_not_all(tmp_path: Path) -> None:
    """The bug this fixes: curating 30 photos with slideshow=slyde pushed all 30, one per minute."""
    h = Harness(tmp_path, Settings(switchbot_slideshow="slyde"))
    h.curate([f"p{i}" for i in range(30)])
    assert h.step(T) == 1
    assert len(h.uploaded) == 1 and len(h.on_air()) == 1
    assert len(h.store.list_library("SB")) == 30  # the rest waits in the library


def test_slyde_mode_one_new_photo_per_interval_each_once(tmp_path: Path) -> None:
    h = Harness(
        tmp_path, Settings(switchbot_slideshow="slyde", switchbot_slideshow_interval_minutes=30)
    )
    h.curate(["p0", "p1", "p2", "p3"])
    shown = []
    for n in range(4):
        h.step(T + timedelta(minutes=30 * n))
        shown += h.on_air()
        assert h.step(T + timedelta(minutes=30 * n + 15)) == 0  # halfway: nothing moves
        assert h.on_air() == shown[-1:]
    assert sorted(shown) == ["p0", "p1", "p2", "p3"]  # each photo once
    assert len(h.uploaded) == 4
    assert h.store.get_frame("SB").last_seen is not None  # type: ignore[union-attr]


def test_deck_spans_curated_uploaded_and_synced_photos(tmp_path: Path) -> None:
    class Immich:
        async def __aenter__(self) -> Immich:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def album_assets(self, album_id: str) -> list[ImmichAsset]:
            return [ImmichAsset(id=f"s{i}", file_name=f"s{i}.jpg", type="IMAGE") for i in range(3)]

    settings = Settings(switchbot_slideshow="slyde", switchbot_slideshow_interval_minutes=10)
    h = Harness(tmp_path, settings)
    h.curate(["c0", "c1"])
    h.library.add("SB", LibraryItem("u0", "u0.jpg", source="upload"))
    sync = FolderSyncService(settings, h.store, h.library, h.ds, Immich)  # type: ignore[arg-type]
    r = asyncio.run(sync.reconcile("SB", "alb", "Italien", now=T))
    assert r.total == 3  # the folder mirrors the whole album
    shown = []
    for n in range(6):
        h.step(T + timedelta(minutes=10 * n))
        shown += h.on_air()
    assert sorted(shown) == ["c0", "c1", "s0", "s1", "s2", "u0"]


def test_frame_mode_swaps_batches_of_ten_without_replacement(tmp_path: Path) -> None:
    h = Harness(
        tmp_path, Settings(switchbot_slideshow="frame", switchbot_slideshow_interval_minutes=60)
    )
    h.curate([f"p{i}" for i in range(25)])
    assert h.step(T) == 10
    b1 = h.on_air()
    assert h.step(T + timedelta(minutes=30)) == 0 and h.on_air() == b1
    assert h.step(T + timedelta(minutes=61)) == 10
    b2 = h.on_air()
    assert set(b1).isdisjoint(b2)
    h.step(T + timedelta(minutes=122))
    b3 = h.on_air()
    assert len(set(b3)) == 10
    assert set(b1) | set(b2) | set(b3) == {f"p{i}" for i in range(25)}
    # Photos of batch 1 that came back in batch 3 were pushed again, not skipped.
    assert len(h.uploaded) == 30


def test_held_batch_is_topped_up_and_undelivered_photos_return(tmp_path: Path) -> None:
    h = Harness(
        tmp_path, Settings(switchbot_slideshow="frame", switchbot_slideshow_interval_minutes=60)
    )
    ids = [f"p{i}" for i in range(30)]
    h.curate(ids)
    h.ds.enqueue_desired("SB", now=T)  # drawn, but never delivered
    batch1 = h.on_air()
    _, rotated_at = h.store.get_shuffle_epoch("SB", DECK)

    # Two photos of the held batch leave the library: refilled now, the interval keeps running.
    h.curate([a for a in ids if a not in batch1[:2]])
    h.ds.enqueue_desired("SB", now=T + timedelta(minutes=10))
    held = h.on_air()
    assert len(held) == 10 and set(batch1[2:]) <= set(held)
    assert h.store.get_shuffle_epoch("SB", DECK)[1] == rotated_at

    # At rotation the held photos never reached the frame: back into the deck, not counted seen.
    h.ds.enqueue_desired("SB", now=T + timedelta(minutes=61))
    assert set(h.on_air()).isdisjoint(held)
    with sqlite3.connect(tmp_path / "d.db") as conn:
        seen = {r[0] for r in conn.execute("SELECT asset_id FROM shuffle_history")}
    assert seen.isdisjoint(held)


def test_leaving_the_batch_drops_queued_and_delivered_rows(tmp_path: Path) -> None:
    """A photo that goes off air loses its delivery row — a pending push is cancelled, and a shown
    photo is pushed again when its turn comes back."""
    h = Harness(tmp_path, Settings(switchbot_slideshow="slyde"))
    h.curate(["a", "b"])
    for key in ("a.jpg", "b.jpg"):  # queued before the slideshow was switched on
        h.store.enqueue_delivery("SB", key, key[0], next_attempt_at=T.isoformat())
    h.step(T)
    rows = h.store.list_deliveries("SB")
    assert [r.key for r in rows] == [f"{h.on_air()[0]}.jpg"] and len(h.uploaded) == 1


def test_quiet_hours_hold_the_photo_but_next_photo_still_advances(tmp_path: Path) -> None:
    h = Harness(
        tmp_path,
        Settings(
            switchbot_slideshow="slyde",
            switchbot_slideshow_interval_minutes=30,
            switchbot_quiet_hours="00:00-23:59",  # always quiet
        ),
    )
    h.curate(["p0", "p1", "p2"])
    h.step(T)  # the first photo always goes out
    first = h.on_air()
    assert len(first) == 1
    assert h.step(T + timedelta(hours=5)) == 0 and h.on_air() == first  # quiet: held
    assert h.step(T + timedelta(hours=5), advance=True) == 1  # "next photo now" ignores quiet
    assert h.on_air() != first


def test_tick_moves_only_slideshow_frames(tmp_path: Path) -> None:
    h = Harness(
        tmp_path, Settings(switchbot_slideshow="slyde", switchbot_slideshow_interval_minutes=5)
    )
    h.curate(["p0", "p1"])
    h.store.upsert_frame(Frame.cloud_push("EF", backend="served"))
    h.library.set_desired("EF", [LibraryItem("x", "x.jpg")])
    # SB's first photo; EF isn't a slideshow frame.
    assert asyncio.run(h.ds.tick_slideshows(now=T)) == 1
    assert asyncio.run(h.ds.tick_slideshows(now=T + timedelta(minutes=1))) == 0
    assert asyncio.run(h.ds.tick_slideshows(now=T + timedelta(minutes=5))) == 1


def test_off_delivers_the_whole_library(tmp_path: Path) -> None:
    h = Harness(tmp_path, Settings())
    h.curate(["p0", "p1", "p2"])
    assert h.step(T) == 3 and h.on_air() == []


# -- per-frame settings (UI) and presence ------------------------------------------------------


class FakeHA:
    """Home Assistant's /api/states for presence entities; flip ``states`` to come and go."""

    def __init__(self, states: dict[str, str]) -> None:
        self.states = states
        self.requests = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests += 1
            assert request.headers["Authorization"] == "Bearer tok"
            if request.url.path == "/api/states":
                return httpx.Response(
                    200,
                    json=[
                        {"entity_id": e, "state": s, "attributes": {"friendly_name": e}}
                        for e, s in self.states.items()
                    ],
                )
            entity = request.url.path.rsplit("/", 1)[-1]
            if entity not in self.states:
                return httpx.Response(404)
            return httpx.Response(200, json={"entity_id": entity, "state": self.states[entity]})

        return httpx.MockTransport(handler)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _presence_harness(tmp_path: Path, ha: FakeHA, clock: Clock, **settings: object) -> Harness:
    s = Settings(
        switchbot_slideshow="slyde",
        switchbot_slideshow_interval_minutes=30,
        ha_base_url="http://ha",
        ha_token="tok",
        **settings,  # type: ignore[arg-type]
    )
    h = Harness(tmp_path, s)
    presence = Presence(s, transport=ha.transport(), clock=clock)
    h.ds._slideshow = Slideshow(s, h.store, h.library, rng=random.Random(1), presence=presence)
    return h


def test_presence_states() -> None:
    assert is_present("home") and is_present("on") and is_present("2")
    assert is_present("not_home") is False and is_present("off") is False
    assert is_present("0") is False  # zone.home: nobody in the zone
    assert is_present("unavailable") is None and is_present("Büro") is None  # neither: unknown


def test_frame_settings_override_the_configured_defaults(tmp_path: Path) -> None:
    settings = Settings(switchbot_slideshow="slyde", switchbot_quiet_hours="22:00-07:00")
    h = Harness(tmp_path, settings)
    show = h.ds.slideshow
    assert show.config("SB").interval_minutes == 60  # the add-on default
    h.store.set_slideshow_setting("SB", 10, "", "zone.home")
    assert show.config("SB") == SlideshowConfig(10, "", "zone.home")

    h.curate(["p0", "p1", "p2"])
    h.store.set_slideshow_setting("SB", 10, "", "")
    h.step(T)
    first = h.on_air()
    assert h.step(T + timedelta(minutes=10)) == 1 and h.on_air() != first  # 10, not 60


def test_slideshow_pauses_while_nobody_is_home(tmp_path: Path) -> None:
    ha = FakeHA({"zone.home": "1"})
    clock = Clock()
    h = _presence_harness(tmp_path, ha, clock, switchbot_presence_entity="zone.home")
    h.curate([f"p{i}" for i in range(5)])
    asyncio.run(h.ds.tick_slideshows(now=T))
    asyncio.run(h.ds.drain(now=T))
    first = h.on_air()

    ha.states["zone.home"] = "0"  # everybody left
    clock.t = 120  # presence cache (60 s) expired
    later = T + timedelta(hours=3)
    assert asyncio.run(h.ds.tick_slideshows(now=later)) == 0
    assert h.on_air() == first  # held while away

    assert h.step(later, advance=True) == 1  # "next photo now" still works while away
    held = h.on_air()

    ha.states["zone.home"] = "2"  # someone is back: the overdue change happens on the next tick
    clock.t = 240
    assert asyncio.run(h.ds.tick_slideshows(now=later + timedelta(minutes=31))) == 1
    assert h.on_air() != held


def test_presence_is_cached_and_fails_open(tmp_path: Path) -> None:
    ha = FakeHA({})  # the entity doesn't exist (typo, deleted): never blocks the slideshow
    clock = Clock()
    h = _presence_harness(tmp_path, ha, clock, switchbot_presence_entity="person.nobody")
    h.curate(["p0", "p1"])
    asyncio.run(h.ds.tick_slideshows(now=T))
    for n in range(5):  # ticks every few seconds within the minute: one request
        asyncio.run(h.ds.tick_slideshows(now=T + timedelta(seconds=5 * n)))
    assert ha.requests == 1
    first = h.on_air()
    clock.t = 61
    assert asyncio.run(h.ds.tick_slideshows(now=T + timedelta(minutes=30))) == 1
    assert h.on_air() != first and ha.requests == 2


def test_slideshow_settings_api(tmp_path: Path) -> None:
    from test_served_backend import ServedHarness

    served = ServedHarness(
        Settings(
            frame_backend="sungale-cloud",
            frame_discovery=False,
            database_url=f"sqlite:///{tmp_path}/m.db",
            cache_dir=f"{tmp_path}/cache",
            switchbot_slideshow="slyde",
            switchbot_slideshow_interval_minutes=180,
            switchbot_quiet_hours="22:00-07:00",
        )
    )
    try:
        served.app.state.store.upsert_frame(Frame.cloud_push("SB", backend="switchbot"))
        got = served.request("GET", "/api/frames/SB/slideshow").json()
        assert got["interval_minutes"] == 180 and got["quiet_hours"] == "22:00-07:00"
        assert got["mode"] == "slyde" and got["defaults"]["interval_minutes"] == 180
        assert got["ha_available"] is False and got["someone_home"] is None

        body = {"interval_minutes": 45, "quiet_hours": "23:30-06:15", "presence_entity": ""}
        put = served.request("PUT", "/api/frames/SB/slideshow", json=body)
        assert put.status_code == 200 and put.json()["interval_minutes"] == 45
        detail = served.request("GET", "/api/frames/SB/detail").json()
        assert detail["slideshow_interval_minutes"] == 45
        assert detail["quiet_hours"] == "23:30-06:15"

        bad = {"interval_minutes": 0, "quiet_hours": "25:00-07:00", "presence_entity": "Zone Home"}
        assert served.request("PUT", "/api/frames/SB/slideshow", json=bad).status_code == 422
        assert served.request("GET", "/api/frames/nope/slideshow").status_code == 404
        assert served.request("GET", "/api/frames/SB/slideshow/presence-entities").json() == []
    finally:
        served.close()

"""Interlude: a recurring non-photo image shown between a frame's slideshow photos (#70).

The behaviour these tests pin down, in order of how much it would hurt to get wrong:

1. the rotation resumes **where it was**, never restarting at the first photo;
2. removing the image returns the frame to its **own** slideshow, fully restored;
3. a **separate process** updates what's on screen just by writing a file;
4. the frame's real settings are captured once, so a restore can't park the frame forever.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from conftest import HOST, PORTS
from memento_core import FrameClient
from memento_core.albums import ALBUM_PHOTOS
from memento_emulator import EmulatedFrame
from slyde_backend.config import Settings
from slyde_backend.frame import Frame
from slyde_backend.frames import FrameService
from slyde_backend.interlude import (
    NEVER_SECONDS,
    InterludeConductor,
    InterludeService,
    InterludeUnavailable,
    managed_image_path,
    supports_interludes,
    validate_image,
)
from slyde_backend.naming import interlude_dest_name, is_reserved_dest
from slyde_backend.store import Store

PHOTOS = ["one.jpg", "two.jpg", "three.jpg"]


def image_bytes(colour: tuple[int, int, int], size: tuple[int, int] = (80, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path}/state.db",
        cache_dir=str(tmp_path / "cache"),
        interlude_dir=str(tmp_path / "interlude"),
        frame_host=HOST,
        frame_discovery=False,
        frame_settle_delay=0,  # tests don't need the real device pacing
        interlude_poll_seconds=0.05,
        interlude_flap_threshold=1,
        frame_canvas="160x120",
    )


@pytest.fixture
def store(settings: Settings) -> Store:
    store = Store(settings.sqlite_path)
    store.upsert_frame(Frame.connected(HOST, backend="memento-lan", name="Test Frame"))
    return store


@pytest.fixture
def conductor(
    frame: EmulatedFrame, settings: Settings, store: Store
) -> Iterator[InterludeConductor]:
    """A conductor over the emulator, with three photos on the frame and in the Library."""
    for index, name in enumerate(PHOTOS):
        frame.state.add_photo(name, image_bytes((index * 60, 100, 200)))
        store.add_library_item(HOST, name, name, source="frame")
        store.mark_delivered(
            store.enqueue_delivery(HOST, name, name, next_attempt_at=datetime.now(UTC).isoformat())
        )
    frame.state.update_config({"DisplayTime": 1, "ShuffleOn": False, "DisplayOn": True})
    frame.state.current_image = PHOTOS[0]
    yield InterludeConductor(
        HOST,
        store=store,
        frames=FrameService(settings, ports=PORTS, store=store),
        settings=settings,
    )


def enable(store: Store, **changes: object) -> None:
    from dataclasses import replace

    store.set_interlude(replace(store.get_interlude(HOST), enabled=True, **changes))  # type: ignore[arg-type]


def write_interlude(settings: Settings, data: bytes) -> Path:
    path = managed_image_path(settings, HOST)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def run_cycles(conductor: InterludeConductor, count: int) -> list[str]:
    """Run ``count`` cycles with the dwell collapsed, returning what was displayed, in order.

    ``_dwell`` is the only place a cycle waits, and it is called immediately after each
    ``display_image`` — so sampling the frame there records the exact on-screen sequence.
    """
    shown: list[str] = []
    frame_state = conductor._frames

    async def sample(_seconds: float) -> None:
        shown.append(await frame_state.get_current_image(HOST))

    conductor._dwell = sample  # type: ignore[method-assign]
    for _ in range(count):
        await conductor._cycle()
    return shown


# -- 1. the cursor -------------------------------------------------------------------------------
def test_rotation_resumes_where_it_left_off_instead_of_restarting(
    conductor: InterludeConductor, settings: Settings
) -> None:
    """The whole point: photo -> interlude -> the NEXT photo, for laps, with no jump back to #1.

    The failure this guards against is silent and total — the frame derives "next" by looking the
    displayed filename up in its album, so resuming from a non-member interlude with ``next_image``
    would restart at photo 1 every single time and the frame would only ever show two pictures.
    """
    write_interlude(settings, image_bytes((255, 0, 0)))
    enable(conductor._store)
    shown = asyncio.run(run_cycles(conductor, 4))

    slot_names = {interlude_dest_name("a"), interlude_dest_name("b")}
    photos_in_order = [name for name in shown if name not in slot_names]
    assert photos_in_order == PHOTOS + PHOTOS[:1]  # a full lap, then wrapping to the start
    assert [name in slot_names for name in shown] == [False, True] * 4  # strict alternation


def test_every_n_photos_puts_the_interlude_after_a_run_of_photos(
    conductor: InterludeConductor, settings: Settings
) -> None:
    write_interlude(settings, image_bytes((0, 255, 0)))
    enable(conductor._store, every_n_photos=3)
    shown = asyncio.run(run_cycles(conductor, 1))
    assert [is_reserved_dest(name) for name in shown] == [False, False, False, True]


# -- 2. removal means normal operation -----------------------------------------------------------
def test_removing_the_image_hands_the_slideshow_back_to_the_frame(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """Delete the image and the frame must go back to being an ordinary photo frame.

    Not just "the conductor stopped": the frame's own slide time and shuffle are restored, a real
    photo is on screen, and the device is actually advancing again under its own timer.
    """
    path = write_interlude(settings, image_bytes((0, 0, 255)))
    enable(conductor._store, dwell_seconds=1)
    asyncio.run(run_cycles(conductor, 2))

    parked = frame.state.config["DisplayTime"]
    assert parked > 1  # engaged: the frame's own timer is parked well beyond our cadence
    assert conductor._store.get_interlude(HOST).engaged is True

    path.unlink()  # <- the separate process withdraws its image
    asyncio.run(run_cycles(conductor, 2))

    row = conductor._store.get_interlude(HOST)
    assert row.engaged is False and row.state == "standby"
    assert frame.state.config["DisplayTime"] == 1  # the user's own slide time, restored
    assert frame.state.config["ShuffleOn"] is False
    assert not is_reserved_dest(frame.state.current_image)  # a real photo is on screen
    # The interlude buffers are off the device too — the frame is exactly as if it never had one.
    assert not any(is_reserved_dest(name) for name in frame.state.photos)

    # And the frame is genuinely advancing again under its own timer (1s), not merely un-parked.
    before = frame.state.current_image
    for _ in range(60):
        if frame.state.current_image != before:
            break
        asyncio.run(asyncio.sleep(0.1))
    assert frame.state.current_image != before, "the frame's own slideshow did not resume"


def test_shuffle_is_restored_when_the_user_had_it_on(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """Slyde turns the frame's shuffle off (it fights the cursor) — and must give it back."""
    frame.state.update_config({"ShuffleOn": True})
    path = write_interlude(settings, image_bytes((10, 10, 10)))
    enable(conductor._store)
    asyncio.run(run_cycles(conductor, 1))
    assert frame.state.config["ShuffleOn"] is False  # off while Slyde owns the cursor

    path.unlink()
    asyncio.run(run_cycles(conductor, 2))
    assert frame.state.config["ShuffleOn"] is True


def test_an_unusable_image_is_treated_as_absent_not_pushed_to_the_frame(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """A producer rewriting a file is caught mid-write routinely; half a JPEG must never display."""
    write_interlude(settings, image_bytes((7, 7, 7))[:120])  # truncated: a partial write
    enable(conductor._store)
    asyncio.run(run_cycles(conductor, 2))

    assert conductor._store.get_interlude(HOST).engaged is False
    assert not any(is_reserved_dest(name) for name in frame.state.photos)
    assert frame.state.config["DisplayTime"] == 1  # never parked, so nothing to restore


def test_a_frame_with_no_delivered_photos_is_left_alone(
    frame: EmulatedFrame, settings: Settings, store: Store
) -> None:
    """With nothing to rotate, conducting would strand the frame parked on one image."""
    write_interlude(settings, image_bytes((1, 2, 3)))
    enable(store)
    conductor = InterludeConductor(
        HOST,
        store=store,
        frames=FrameService(settings, ports=PORTS, store=store),
        settings=settings,
    )
    asyncio.run(run_cycles(conductor, 1))
    assert store.get_interlude(HOST).engaged is False
    assert store.get_interlude(HOST).state == "standby"


def test_the_panel_going_off_suspends_the_interlude_and_clears_the_buffers(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """An interlude engine must not push images at a frame the owner switched off — and standing
    down must take its buffers off the device.

    The buffers matter because every uploaded file joins the frame's reserved "Photos" album, which
    is exactly the album its own slideshow cycles. A buffer left behind would show up among the
    photos once the frame is running itself again — a stale dashboard interleaved with the family
    pictures, with nothing running to explain it.
    """
    write_interlude(settings, image_bytes((9, 9, 9)))
    enable(conductor._store)
    asyncio.run(run_cycles(conductor, 1))
    assert any(is_reserved_dest(name) for name in frame.state.photos)  # engaged: a buffer is there

    frame.state.update_config({"DisplayOn": False})
    asyncio.run(run_cycles(conductor, 2))

    assert conductor._store.get_interlude(HOST).engaged is False
    assert not any(is_reserved_dest(name) for name in frame.state.photos)
    photos_album = frame.state.albums.get(ALBUM_PHOTOS)
    assert photos_album is not None
    assert not any(is_reserved_dest(name) for name in photos_album.images)


def test_a_clean_shutdown_leaves_the_frame_with_no_trace_of_the_interlude(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """Restarting the manager must not interleave last hour's dashboard with the photos."""
    write_interlude(settings, image_bytes((3, 3, 3)))
    enable(conductor._store)
    asyncio.run(run_cycles(conductor, 1))

    service = InterludeService(
        conductor._store, FrameService(settings, ports=PORTS, store=conductor._store), settings
    )
    asyncio.run(service.stop())

    assert frame.state.config["DisplayTime"] == 1  # the user's own slide time is back
    assert not any(is_reserved_dest(name) for name in frame.state.photos)
    assert conductor._store.get_interlude(HOST).engaged is False


# -- 3. a separate process drives the display ----------------------------------------------------
def test_a_separate_process_changes_what_the_frame_shows_by_writing_the_file(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """Write new bytes; the very next interlude shows them — and the buffers alternate.

    Alternating slots is what stops an upload from landing on the file the frame is displaying
    right now.
    """
    write_interlude(settings, image_bytes((200, 0, 0)))
    enable(conductor._store)
    asyncio.run(run_cycles(conductor, 1))
    first_slot = conductor._store.get_interlude(HOST).slot
    first_bytes = frame.state.photos[interlude_dest_name(first_slot)]

    write_interlude(settings, image_bytes((0, 200, 0)))  # <- the separate process publishes
    asyncio.run(run_cycles(conductor, 1))
    row = conductor._store.get_interlude(HOST)
    assert row.slot != first_slot, "a changed image must not overwrite the on-screen buffer"
    assert frame.state.photos[interlude_dest_name(row.slot)] != first_bytes
    assert frame.state.current_image == interlude_dest_name(row.slot)


def test_unchanged_bytes_are_never_re_uploaded(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """A producer that rewrites an identical image costs nothing on the wire."""
    write_interlude(settings, image_bytes((123, 45, 67)))
    enable(conductor._store)
    asyncio.run(run_cycles(conductor, 1))
    row = conductor._store.get_interlude(HOST)

    uploaded = [name for name in frame.state.photos if is_reserved_dest(name)]
    asyncio.run(run_cycles(conductor, 3))  # same bytes, three more laps
    assert [name for name in frame.state.photos if is_reserved_dest(name)] == uploaded
    assert conductor._store.get_interlude(HOST).slot == row.slot


def test_the_interlude_is_fitted_to_the_panel_without_cropping(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """A dashboard must arrive whole: the interlude defaults to ``contain``, unlike a photo."""
    write_interlude(settings, image_bytes((30, 60, 90), size=(400, 100)))  # very wide
    enable(conductor._store)
    asyncio.run(run_cycles(conductor, 1))
    slot = conductor._store.get_interlude(HOST).slot
    with Image.open(io.BytesIO(frame.state.photos[interlude_dest_name(slot)])) as im:
        assert im.size == settings.canvas


# -- 4. restore state ----------------------------------------------------------------------------
def test_the_frames_real_settings_are_captured_once_not_re_captured_while_engaged(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """Re-capturing while engaged would save the PARKED value as "the original" — and a restore
    would then park the frame permanently, with no way back from the UI."""
    write_interlude(settings, image_bytes((5, 5, 5)))
    enable(conductor._store)
    asyncio.run(run_cycles(conductor, 3))
    assert conductor._store.get_interlude(HOST).saved_display_time == 1


def test_a_frame_left_parked_by_a_crash_is_restored_on_startup(
    frame: EmulatedFrame, settings: Settings, store: Store
) -> None:
    """The crash safety net: a parked frame with nobody conducting sits on one image forever."""
    from dataclasses import replace

    frame.state.update_config({"DisplayTime": NEVER_SECONDS})
    store.set_interlude(
        replace(
            store.get_interlude(HOST),
            enabled=True,
            engaged=True,
            saved_display_time=45,
            saved_shuffle=True,
            last_photo=PHOTOS[1],
        )
    )
    frame.state.add_photo(PHOTOS[1], image_bytes((4, 4, 4)))
    frame.state.add_photo(interlude_dest_name("a"), image_bytes((6, 6, 6)))
    frame.state.current_image = interlude_dest_name("a")

    service = InterludeService(store, FrameService(settings, ports=PORTS, store=store), settings)
    assert asyncio.run(service.recover()) == 1
    assert frame.state.config["DisplayTime"] == 45
    assert frame.state.config["ShuffleOn"] is True
    assert frame.state.current_image == PHOTOS[1]  # a real photo, not the interlude
    assert store.get_interlude(HOST).engaged is False
    # ...and the stale buffer is gone, so it can't reappear in the frame's own rotation.
    assert not any(is_reserved_dest(name) for name in frame.state.photos)


def test_a_conductor_that_finds_the_frame_already_parked_restores_a_sane_slide_time(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """If a crash left the frame parked but the row lost, the parked value must not become "the
    user's setting" — that would park the frame forever on the next restore."""
    frame.state.update_config({"DisplayTime": NEVER_SECONDS})
    write_interlude(settings, image_bytes((2, 2, 2)))
    enable(conductor._store)
    asyncio.run(run_cycles(conductor, 1))
    assert conductor._store.get_interlude(HOST).saved_display_time == 60


def test_the_park_is_finite_so_the_frames_own_timer_is_a_dead_man_switch(
    conductor: InterludeConductor, settings: Settings, frame: EmulatedFrame
) -> None:
    """Measured on fw 6.02: DisplayImage re-arms the frame's countdown and arbitrary second values
    are honoured. So we park at a finite value longer than our own cadence — our transitions keep
    re-arming it, but if Slyde dies the frame resumes its own slideshow instead of holding one
    picture until someone restarts the manager."""
    write_interlude(settings, image_bytes((8, 8, 8)))
    enable(conductor._store, dwell_seconds=30)
    asyncio.run(run_cycles(conductor, 1))

    parked = frame.state.config["DisplayTime"]
    assert parked < NEVER_SECONDS, "a finite park is what makes the frame self-recover"
    assert parked > 30, "must exceed the longest gap between our own commands, or it fires on us"


def test_an_explicit_park_setting_overrides_the_computed_one(
    frame: EmulatedFrame, store: Store, tmp_path: Path
) -> None:
    """Operators can still pin it to 'never' -- the frame then holds the last image until Slyde
    restarts and the startup watchdog restores it."""
    from dataclasses import replace as _replace

    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/s.db",
        cache_dir=str(tmp_path / "c"),
        interlude_dir=str(tmp_path / "i"),
        frame_host=HOST,
        frame_discovery=False,
        frame_settle_delay=0,
        interlude_poll_seconds=0.05,
        interlude_flap_threshold=1,
        interlude_park_seconds=NEVER_SECONDS,
        frame_canvas="160x120",
    )
    frame.state.add_photo(PHOTOS[0], image_bytes((1, 1, 1)))
    store.add_library_item(HOST, PHOTOS[0], PHOTOS[0], source="frame")
    store.mark_delivered(
        store.enqueue_delivery(
            HOST, PHOTOS[0], PHOTOS[0], next_attempt_at=datetime.now(UTC).isoformat()
        )
    )
    frame.state.update_config({"DisplayTime": 1, "DisplayOn": True, "ShuffleOn": False})
    store.set_interlude(_replace(store.get_interlude(HOST), enabled=True))
    write_interlude(settings, image_bytes((2, 2, 2)))

    conductor = InterludeConductor(
        HOST,
        store=store,
        frames=FrameService(settings, ports=PORTS, store=store),
        settings=settings,
    )
    asyncio.run(run_cycles(conductor, 1))
    assert frame.state.config["DisplayTime"] == NEVER_SECONDS


# -- isolation from the photo library ------------------------------------------------------------
def test_reserved_files_are_never_imported_as_photos(
    frame: EmulatedFrame, settings: Settings, store: Store, tmp_path: Path
) -> None:
    """Importing a frame's photos must skip Slyde's own files, or a clock joins the Library."""
    import asyncio as _asyncio

    from slyde_backend.frame_import import import_frame_photos
    from slyde_backend.imagecache import ImageCache as _Cache
    from slyde_backend.library import FrameLibrary
    from slyde_backend.previews import AssetPreviewCache

    frame.state.add_photo("holiday.jpg", image_bytes((11, 22, 33)))
    frame.state.add_photo(interlude_dest_name("a"), image_bytes((44, 55, 66)))
    cache = _Cache(str(tmp_path / "c"))
    result = _asyncio.run(
        import_frame_photos(
            frame=Frame.connected(HOST, backend="memento-lan"),
            frame_service=FrameService(settings, ports=PORTS, store=store),
            settings=settings,
            image_cache=cache,
            asset_previews=AssetPreviewCache(str(tmp_path / "p")),
            uploads=_Cache(str(tmp_path / "u")),
            library=FrameLibrary(store, cache),
            store=store,
        )
    )
    names = {dest for _a, dest, _s, _f in store.list_library(HOST)}
    assert names == {"holiday.jpg"}
    assert result.total == 1


def test_the_frame_card_hero_image_ignores_a_displayed_interlude(
    frame: EmulatedFrame, settings: Settings, store: Store
) -> None:
    """Otherwise the overview would show the clock as the frame's picture, half the time."""
    frame.state.add_photo(interlude_dest_name("a"), image_bytes((77, 88, 99)))
    frame.state.current_image = interlude_dest_name("a")
    service = FrameService(settings, ports=PORTS, store=store)
    assert asyncio.run(service.get_current_thumbnail(HOST, photos_only=True)) is None
    assert asyncio.run(service.get_current_thumbnail(HOST)) is not None  # unfiltered still works


# -- capability gate -----------------------------------------------------------------------------
def test_only_full_colour_connected_frames_declare_interlude_support() -> None:
    """E-paper panels are excluded by capability, not by a colour-model check in the engine."""
    assert supports_interludes("memento-lan") is True
    assert supports_interludes("sungale-cloud") is False  # e-paper + the frame polls us
    assert supports_interludes("switchbot") is False  # e-paper + no display-by-name
    assert supports_interludes("nope") is False


# -- protocol / emulator fidelity ----------------------------------------------------------------
def test_the_dedicated_duration_and_shuffle_commands_change_the_frames_config(
    frame: EmulatedFrame,
) -> None:
    """The conductor parks the slideshow with ``ChangePictureDuration``, as the official app does —
    not a whole-config write that would round-trip the cleartext Wi-Fi password every slide."""
    with FrameClient(HOST, ports=PORTS) as client:
        client.change_picture_duration(2419200)
        client.change_shuffle(True)
        config = client.get_config()
    assert config["DisplayTime"] == 2419200
    assert config["ShuffleOn"] is True
    assert "PictureDuration" not in config  # the payload key must not leak into the config


def test_validate_image_rejects_a_partial_write() -> None:
    whole = image_bytes((1, 1, 1))
    assert validate_image(whole) == whole
    with pytest.raises(InterludeUnavailable):
        validate_image(whole[: len(whole) // 2])
    with pytest.raises(InterludeUnavailable):
        validate_image(b"")


def test_interlude_state_survives_a_frame_rekey(store: Store, settings: Settings) -> None:
    """A DHCP-driven move onto a stable GUID must carry the interlude config with it (#58)."""
    from dataclasses import replace

    store.set_interlude(replace(store.get_interlude(HOST), enabled=True, source_ref="/tmp/x.png"))
    store.rekey_frame(HOST, "guid-1234")
    assert store.get_interlude("guid-1234").source_ref == "/tmp/x.png"
    assert store.get_interlude(HOST).enabled is False  # the old key keeps nothing


def test_deregistering_a_frame_removes_its_interlude_row(store: Store) -> None:
    from dataclasses import replace

    store.set_interlude(replace(store.get_interlude(HOST), enabled=True))
    store.purge_frame(HOST)
    assert store.get_interlude(HOST).enabled is False


def test_managed_image_path_is_per_frame_and_filesystem_safe(settings: Settings) -> None:
    path = managed_image_path(settings, "192.168.1.5")
    assert path.parent == Path(settings.interlude_dir)
    assert json.dumps(str(path))  # no surprises in the name
    assert managed_image_path(settings, "../evil") != managed_image_path(settings, "other")
    assert ".." not in managed_image_path(settings, "../evil").name

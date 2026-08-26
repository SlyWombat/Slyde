"""Interlude: a recurring non-photo image shown between a frame's slideshow photos (#70).

The frame alternates ``photo -> interlude -> the next photo -> interlude -> ...``, where the
interlude's *content* is produced by something else entirely -- a clock, a weather board, a
dashboard, a transit display -- and refreshed as often as that producer likes. Slyde does not
render it; it only makes sure whatever bytes the producer last published are what the frame shows
in the interlude slot.

Two rules make this work, and both are load-bearing:

**1. While engaged, Slyde owns the slideshow cursor.** The frame's own slide timer is *parked*
(``ChangePictureDuration`` at ``INTERLUDE_PARK_SECONDS``, the protocol's "never"), so the frame
never self-advances, and every transition is an explicit ``DisplayImage``. Resuming the rotation is
therefore ``display_image(rotation[i + 1])`` -- never ``next_image()``, because the frame derives
"next" by looking the *currently displayed* filename up in its current album, and from a non-member
file (an interlude buffer) that lookup misses and the rotation silently restarts at the first
photo. The frame's own shuffle is turned off for the same reason: it would fight the cursor. Slyde
shuffles its own list instead.

**2. No image means normal operation, not a frozen frame.** Parking the frame's timer makes Slyde
responsible for the slideshow, so the conductor stands down -- restores the frame's original slide
time and shuffle, leaves a real photo on screen, and lets the frame run its own slideshow again --
whenever the interlude image is absent, unreadable, disabled, or the panel is off. It keeps
watching, and re-engages when the image comes back. A crash while engaged is covered by the same
path: the durable ``engaged`` + ``saved_*`` state in the store lets the next startup restore the
frame before doing anything else.

The image itself comes from an :class:`InterludeSource`. The shipped one reads a **file**, which is
the whole "a separate process updates the display" story: any program that can write a file can
drive the slot, and ``rm`` of that file returns the frame to a plain photo slideshow. A ``url``
source (HTTP GET) covers producers that would rather be polled.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path

import httpx
from PIL import Image

from .backends import get_backend
from .config import Settings
from .frames import FrameService, FrameUnavailable
from .naming import (
    INTERLUDE_SLOTS,
    interlude_dest_name,
    is_reserved_dest,
    other_interlude_slot,
)
from .processing import ProcessingProfile, prepare, profile_for
from .store import InterludeRow, Store

_log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]")

# States reported to the UI. ``standby`` is the good, expected resting state when there is no
# interlude image: the frame is running its own slideshow exactly as it would without this feature.
STATE_IDLE = "idle"  # not enabled for this frame
STATE_ENGAGED = "engaged"  # Slyde is conducting; interlude appears between photos
STATE_STANDBY = "standby"  # enabled, but no usable image (or panel off) -- frame runs itself
STATE_UNSUPPORTED = "unsupported"  # this frame's backend can't do interludes


def supports_interludes(backend_name: str) -> bool:
    """Whether a backend's frames can show interludes, from its declared capabilities (ADR-009)."""
    try:
        return bool(getattr(get_backend(backend_name).capabilities, "interludes", False))
    except ValueError:
        return False


def managed_image_path(settings: Settings, frame_id: str) -> Path:
    """Where Slyde keeps a frame's interlude image when no explicit path is configured.

    This is the drop point a separate process writes to (directly, or through the upload endpoint).
    """
    base = settings.interlude_dir or f"{settings.cache_dir}/interlude"
    return Path(base) / f"{(_SAFE.sub('_', frame_id).strip('._') or '_')[:200]}.img"


class InterludeUnavailable(Exception):
    """The interlude image isn't there (or isn't usable). Not an error -- it means 'stand down'."""


# -- sources -------------------------------------------------------------------------------------
class InterludeSource(ABC):
    """Where an interlude image comes from. ``load()`` returns bytes, or raises to mean 'absent'."""

    @abstractmethod
    async def load(self) -> bytes:
        """The current image bytes, or raise :class:`InterludeUnavailable` if there isn't one."""

    @abstractmethod
    def describe(self) -> str:
        """A short human-readable description of where the image comes from."""


class FileInterludeSource(InterludeSource):
    """Read the image from a path a separate process writes.

    A producer rewriting a file every minute is *routinely* caught mid-write, so the bytes are
    decode-validated before they're ever considered usable: a truncated read is treated exactly
    like an absent file, and the frame keeps showing the last good interlude instead of half a
    JPEG. Deleting the file is the documented way to hand the frame back to its normal slideshow.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self) -> bytes:
        try:
            data = await asyncio.to_thread(self._path.read_bytes)
        except FileNotFoundError as exc:
            raise InterludeUnavailable(f"no interlude image at {self._path}") from exc
        except OSError as exc:
            raise InterludeUnavailable(f"cannot read {self._path}: {exc}") from exc
        if not data:
            raise InterludeUnavailable(f"{self._path} is empty (mid-write?)")
        return data

    def describe(self) -> str:
        return str(self._path)


class UrlInterludeSource(InterludeSource):
    """Fetch the image over HTTP for producers that would rather be polled than write a file."""

    def __init__(self, url: str, *, timeout: float) -> None:
        self._url = url
        self._timeout = timeout

    async def load(self) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._url)
                response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise InterludeUnavailable(f"fetch failed: {exc}") from exc
        if not response.content:
            raise InterludeUnavailable("fetch returned no bytes")
        return response.content

    def describe(self) -> str:
        return self._url


def source_for(row: InterludeRow, settings: Settings) -> InterludeSource:
    """Build the configured source for a frame (a path by default, so a file drop just works)."""
    if row.source_kind == "url":
        return UrlInterludeSource(row.source_ref, timeout=settings.interlude_fetch_timeout)
    path = Path(row.source_ref) if row.source_ref else managed_image_path(settings, row.frame_id)
    return FileInterludeSource(path)


def validate_image(data: bytes) -> bytes:
    """Decode-verify ``data``, raising :class:`InterludeUnavailable` if it isn't a whole image.

    The guard against a producer's partial write. ``Image.verify`` reads the whole stream, so a
    truncated file fails here rather than being pushed to the frame.
    """
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception as exc:  # any decode failure means "not a usable image right now"
        raise InterludeUnavailable(f"not a decodable image ({exc})") from exc
    return data


# -- the conductor -------------------------------------------------------------------------------
class InterludeConductor:
    """Drives one frame's photo/interlude alternation, and stands down when it shouldn't.

    One instance per frame, owned by :class:`InterludeService`. All frame I/O goes through
    ``FrameService``, which serializes and paces control ops per device.
    """

    def __init__(
        self,
        frame_id: str,
        *,
        store: Store,
        frames: FrameService,
        settings: Settings,
    ) -> None:
        self.frame_id = frame_id
        self._store = store
        self._frames = frames
        self._settings = settings
        self._cursor = 0
        self._last_displayed = ""
        self._present_streak = 0
        self._absent_streak = 0
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Interrupt the current dwell so a settings change takes effect now, not next slide."""
        self._wake.set()

    # -- state -----------------------------------------------------------------------------------
    def _row(self) -> InterludeRow:
        return self._store.get_interlude(self.frame_id)

    def _save(self, row: InterludeRow, **changes: object) -> InterludeRow:
        updated = replace(row, **changes)  # type: ignore[arg-type]
        self._store.set_interlude(updated)
        return updated

    def _rotation(self) -> list[str]:
        """The photos to cycle: the frame's Library, in order, limited to what's actually on the
        device (delivered), with Slyde-reserved files excluded so the interlude can never become a
        member of its own rotation."""
        delivered = self._store.delivered_payloads(self.frame_id)
        return [
            dest
            for _asset, dest, _source, _folder in self._store.list_library(self.frame_id)
            if dest in delivered and not is_reserved_dest(dest)
        ]

    def _profile(self, row: InterludeRow) -> ProcessingProfile:
        frame = self._store.get_frame(self.frame_id)
        assert frame is not None
        profile = profile_for(frame, self._settings, canvas=self._settings.canvas)
        # A clock or dashboard must never be cropped, so the interlude carries its own fit -- it
        # defaults to ``contain`` where photos default to ``smart``.
        return replace(profile, fit=row.fit or "contain")

    # -- engage / stand down ---------------------------------------------------------------------
    async def _engage(self, row: InterludeRow) -> InterludeRow:
        """Take over the slideshow: park the frame's timer and turn its own shuffle off.

        The frame's real settings are captured **only on this transition**. Re-capturing them while
        already engaged would record the parked value as "the original" and leave the frame parked
        forever after a restore -- the one failure in this design that a user can't undo from the
        UI.
        """
        if row.engaged:
            return row
        config = await self._frames.get_config(self.frame_id)
        saved_time = int(float(config.get("DisplayTime", 60) or 60))
        saved_shuffle = bool(config.get("ShuffleOn", False))
        if saved_time >= self._settings.interlude_park_seconds:
            # Already parked -- we're recovering from a crash that never got to restore. Trusting
            # this as "the original" would park the frame permanently; fall back to a sane slide
            # time so the user always gets a working slideshow back.
            _log.warning(
                "interlude: frame %s was already parked (DisplayTime=%s); assuming a crashed "
                "conductor and restoring to 60s on stand-down",
                self.frame_id,
                saved_time,
            )
            saved_time = 60
        row = self._save(
            row,
            engaged=True,
            saved_display_time=saved_time,
            saved_shuffle=saved_shuffle,
            state=STATE_ENGAGED,
            detail="",
        )
        await self._frames.set_picture_duration(
            self.frame_id, self._settings.interlude_park_seconds
        )
        if saved_shuffle:
            await self._frames.set_shuffle(self.frame_id, False)
        _log.info(
            "interlude: engaged on frame %s (slide time %ss, shuffle %s saved)",
            self.frame_id,
            saved_time,
            saved_shuffle,
        )
        return row

    async def _stand_down(self, row: InterludeRow, detail: str) -> InterludeRow:
        """Hand the slideshow back to the frame: restore its slide time + shuffle, show a photo.

        This is what "the image was removed, so normal operation continues" actually means -- the
        frame is put back exactly as the user had it and runs its own slideshow again. Called for a
        removed/unusable image, for a disabled interlude, and by the startup watchdog after a crash.
        """
        state = STATE_IDLE if not row.enabled else STATE_STANDBY
        if not row.engaged:
            if row.state != state or row.detail != detail:
                row = self._save(row, state=state, detail=detail)
            return row
        restore_time = row.saved_display_time or 60
        await self._frames.set_picture_duration(self.frame_id, restore_time)
        if row.saved_shuffle:
            await self._frames.set_shuffle(self.frame_id, True)
        # Never leave the interlude on screen: put the last real photo back before the frame's own
        # timer takes over (from a reserved file, its "next" lookup would restart the rotation).
        with contextlib.suppress(FrameUnavailable):
            current = await self._frames.get_current_image(self.frame_id)
            if current and is_reserved_dest(current) and row.last_photo:
                await self._frames.display_image(self.frame_id, row.last_photo)
        # Always take the buffers off the frame. Every uploaded file joins the device's reserved
        # "Photos" album -- which is the album its own slideshow cycles -- so a buffer left behind
        # would keep turning up among the photos once the frame is running itself again, whether we
        # stood down because the image was withdrawn, because the panel went off, or because the
        # manager is shutting down. Re-engaging costs one upload; a stale dashboard in someone's
        # photo rotation costs them trust in the feature.
        for slot in INTERLUDE_SLOTS:
            with contextlib.suppress(Exception):
                await self._frames.delete_photo(self.frame_id, interlude_dest_name(slot))
        _log.info("interlude: stood down on frame %s (%s)", self.frame_id, detail or "no reason")
        return self._save(row, engaged=False, state=state, detail=detail, slot="", content_hash="")

    # -- the image -------------------------------------------------------------------------------
    async def _publish(self, row: InterludeRow, data: bytes) -> InterludeRow:
        """Put ``data`` on the frame as the *next* buffer slot, if it isn't already there.

        Unchanged bytes cost nothing: the content hash short-circuits before any processing or
        upload, so a producer that rewrites an identical image never touches the network. A changed
        image lands in the slot that is NOT on screen, so the file the frame is displaying is never
        overwritten underneath it.
        """
        digest = hashlib.sha256(data).hexdigest()
        if digest == row.content_hash and row.slot:
            return row
        target = other_interlude_slot(row.slot) if row.slot else INTERLUDE_SLOTS[0]
        dest = interlude_dest_name(target)
        prepared = await asyncio.to_thread(prepare, data, self._profile(row))
        await self._frames.upload_images(self.frame_id, [(prepared, dest)], album=None)
        return self._save(row, slot=target, content_hash=digest)

    async def _load_image(self, row: InterludeRow) -> bytes:
        return validate_image(await source_for(row, self._settings).load())

    # -- the loop --------------------------------------------------------------------------------
    async def _dwell(self, seconds: float) -> None:
        """Wait out a slide, but wake early on a settings change (and never sleep unbounded).

        Sliced at the poll interval so a *removed* image is noticed within one poll rather than one
        whole slide -- with a 5-minute slide time, that's the difference between the frame returning
        to normal operation in seconds and doing so five minutes later.
        """
        remaining = max(0.0, seconds)
        step = max(0.5, self._settings.interlude_poll_seconds)
        while remaining > 0:
            slice_ = min(step, remaining)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=slice_)
                self._wake.clear()
                return
            remaining -= slice_

    def _note_presence(self, present: bool) -> bool | None:
        """Debounced view of whether an image is there: True/False once ``threshold`` polls agree.

        A producer that truncates and rewrites its file in place looks briefly absent every single
        refresh; without this, that would drive a full engage/stand-down cycle (two whole-frame
        config writes) every minute.
        """
        threshold = self._settings.interlude_flap_threshold
        if present:
            self._present_streak += 1
            self._absent_streak = 0
        else:
            self._absent_streak += 1
            self._present_streak = 0
        if self._present_streak >= threshold:
            return True
        if self._absent_streak >= threshold:
            return False
        return None

    async def _cycle(self) -> float:
        """One pass: show a photo, then (if there's an image) the interlude. Returns a wait."""
        row = self._row()
        poll = self._settings.interlude_poll_seconds

        if not row.enabled:
            await self._stand_down(row, "")
            return poll
        if not supports_interludes(self._store.get_frame(self.frame_id).backend):  # type: ignore[union-attr]
            self._save(row, state=STATE_UNSUPPORTED, detail="this frame can't show interludes")
            return poll * 12

        # Is there a usable image? Absence is a normal state, not an error.
        data: bytes | None = None
        reason = ""
        try:
            data = await self._load_image(row)
        except InterludeUnavailable as exc:
            reason = str(exc)
        settled = self._note_presence(data is not None)
        if settled is False or (settled is None and not row.engaged):
            # No image (or not yet confidently present): the frame runs its own slideshow.
            await self._stand_down(row, reason or "waiting for an interlude image")
            return poll
        if data is None:
            return poll  # a flap while engaged -- keep the last good interlude, re-check shortly

        # The panel being off/asleep is the user's call; don't wake it or push to a dark screen.
        config = await self._frames.get_config(self.frame_id)
        if not bool(config.get("DisplayOn", True)) or bool(config.get("NightModeOn", False)):
            await self._stand_down(row, "panel is off / night mode")
            return poll * 6

        rotation = self._rotation()
        if not rotation:
            await self._stand_down(row, "no photos delivered to this frame yet")
            return poll * 6

        row = await self._engage(row)
        photo_seconds = float(row.saved_display_time or 60)
        dwell = float(row.dwell_seconds or row.saved_display_time or 60)

        # Re-seat the cursor on reality: someone may have hit next/previous in the UI, or the frame
        # may have been power-cycled, or we may have just engaged. Whatever real photo is on screen
        # is where we continue *from* -- the cursor points AT it, not past it, so the picture the
        # frame is already showing gets its slide rather than being skipped.
        with contextlib.suppress(FrameUnavailable):
            current = await self._frames.get_current_image(self.frame_id)
            if current and not is_reserved_dest(current) and current != self._last_displayed:
                with contextlib.suppress(ValueError):
                    self._cursor = rotation.index(current)

        for _ in range(max(1, row.every_n_photos)):
            photo = rotation[self._cursor % len(rotation)]
            self._cursor = (self._cursor + 1) % len(rotation)
            await self._frames.display_image(self.frame_id, photo)
            self._last_displayed = photo
            row = self._save(row, last_photo=photo)
            # Prepare + upload the interlude WHILE a photo is on screen, so what appears in the
            # slot is the newest bytes the producer had, not a copy fetched a slide ago.
            loop = asyncio.get_running_loop()
            started = loop.time()
            with contextlib.suppress(InterludeUnavailable, FrameUnavailable):
                row = await self._publish(row, data)
            await self._dwell(photo_seconds - (loop.time() - started))

        if row.slot:
            await self._frames.display_image(self.frame_id, interlude_dest_name(row.slot))
            await self._dwell(dwell)
        return 0.0

    async def run(self) -> None:
        """Conduct this frame until cancelled, surviving a frame that comes and goes."""
        while True:
            try:
                wait = await self._cycle()
            except asyncio.CancelledError:
                raise
            except FrameUnavailable as exc:
                # Offline/asleep: don't touch stored state (we may still be engaged, and the
                # restore data must survive). Back off and try again.
                self._store.set_interlude(replace(self._row(), detail=f"frame unreachable: {exc}"))
                wait = self._settings.interlude_poll_seconds * 4
            except Exception:
                _log.exception("interlude: cycle failed for frame %s", self.frame_id)
                wait = self._settings.interlude_poll_seconds * 4
            if wait > 0:
                await self._dwell(wait)


class InterludeService:
    """Supervises one :class:`InterludeConductor` per frame, and recovers after a crash.

    Started by the app lifespan. On startup it first restores any frame left engaged by a previous
    process -- a frame whose slide timer is parked with nobody conducting would sit on one image
    indefinitely -- and only then starts conducting.
    """

    def __init__(self, store: Store, frames: FrameService, settings: Settings) -> None:
        self._store = store
        self._frames = frames
        self._settings = settings
        self._conductors: dict[str, InterludeConductor] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._supervisor: asyncio.Task[None] | None = None
        # Hold a strong reference to fire-and-forget restores: asyncio only keeps a weak one, so an
        # unreferenced task can be garbage-collected mid-restore and leave the frame parked.
        self._releases: set[asyncio.Task[None]] = set()

    def _conductor(self, frame_id: str) -> InterludeConductor:
        conductor = self._conductors.get(frame_id)
        if conductor is None:
            conductor = InterludeConductor(
                frame_id,
                store=self._store,
                frames=self._frames,
                settings=self._settings,
            )
            self._conductors[frame_id] = conductor
        return conductor

    async def recover(self) -> int:
        """Restore any frame a previous process left parked. Returns how many were restored."""
        restored = 0
        for row in self._store.list_interludes():
            if not row.engaged:
                continue
            try:
                await self._conductor(row.frame_id)._stand_down(row, "restored after a restart")
                restored += 1
            except FrameUnavailable:
                # Can't reach it now; the row stays engaged so the next attempt tries again.
                _log.warning("interlude: frame %s unreachable during recovery", row.frame_id)
            except Exception:
                _log.exception("interlude: recovery failed for frame %s", row.frame_id)
        return restored

    def start(self) -> None:
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(self._supervise())

    async def _supervise(self) -> None:
        with contextlib.suppress(Exception):
            await self.recover()
        while True:
            with contextlib.suppress(Exception):
                self._sync_tasks()
            await asyncio.sleep(max(1.0, self._settings.interlude_poll_seconds))

    def _sync_tasks(self) -> None:
        """Run a conductor for every frame with an interlude row; drop the rest."""
        wanted = {r.frame_id for r in self._store.list_interludes() if r.enabled}
        for frame_id in wanted - set(self._tasks):
            self._tasks[frame_id] = asyncio.create_task(self._conductor(frame_id).run())
        for frame_id in set(self._tasks) - wanted:
            task = self._tasks.pop(frame_id)
            task.cancel()
            # A frame whose interlude was just switched off is still parked -- hand it back.
            release = asyncio.create_task(self._release(frame_id))
            self._releases.add(release)
            release.add_done_callback(self._releases.discard)
        for frame_id, task in list(self._tasks.items()):
            if task.done():  # a conductor that died must not silently stop conducting
                del self._tasks[frame_id]

    async def _release(self, frame_id: str) -> None:
        with contextlib.suppress(Exception):
            row = self._store.get_interlude(frame_id)
            await self._conductor(frame_id)._stand_down(row, "interlude turned off")

    def wake(self, frame_id: str) -> None:
        """Nudge a frame's conductor so a settings change applies immediately."""
        conductor = self._conductors.get(frame_id)
        if conductor is not None:
            conductor.wake()
        if self._supervisor is not None:
            self._sync_tasks()

    async def stop(self) -> None:
        """Cancel every conductor and hand each engaged frame back to its own slideshow."""
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None
        for frame_id, task in list(self._tasks.items()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            del self._tasks[frame_id]
        # Restoring on a clean shutdown means a normal restart never leaves a parked frame; the
        # durable state + startup recovery cover the unclean case.
        for row in self._store.list_interludes():
            if row.engaged:
                with contextlib.suppress(Exception):
                    await self._conductor(row.frame_id)._stand_down(row, "manager shutting down")

"""Frame service: an async façade over the synchronous ``memento_core`` client.

Blocking socket operations run in a worker thread so they don't stall the event loop. Operations
are host-parameterized so the app can manage several frames; the host is resolved from an explicit
value, config, or discovery — never hardcoded.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from memento_core import AlbumData, FrameInfo, Ports, Setup

from .backends import ConnectedFrameBackend, FrameConnection, get_backend
from .config import Settings
from .frame import NULL_GUID, Frame
from .naming import is_reserved_dest
from .previews import AssetPreviewCache, current_preview_key, render_canonical_preview
from .store import Store
from .warm import WarmSessionPool, WarmSessionUnavailable

_log = logging.getLogger(__name__)

T = TypeVar("T")


def _candidate_from_config(ip: str, cfg: dict[str, Any]) -> FrameInfo:
    """Describe a scanned frame from its config read, for the onboarding picker (not registered)."""

    def fnum(v: object) -> float:
        try:
            return float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    return FrameInfo(
        name=str(cfg.get("Name", "")),
        ip=ip,
        guid=str(cfg.get("GUID", "")).strip(),
        size=int(str(cfg.get("ScreenSize", "0")) or 0),
        orientation=str(cfg.get("Orientation", "")),
        softver=fnum(cfg.get("SoftwareVersion", 0)),
    )


class FrameUnavailable(RuntimeError):
    """Raised when no frame can be resolved or reached."""


class FrameService:
    def __init__(
        self, settings: Settings, *, ports: Ports | None = None, store: Store | None = None
    ) -> None:
        self._settings = settings
        self._ports = ports or Ports()
        # Which kind of frame we drive (LAN, cloud, …) — selected by config, never hardcoded.
        self._backend = get_backend(settings.frame_backend)
        # Optional registry of known frames (transport-independent). None disables registration.
        self._store = store
        # One control op per physical frame at a time — low-power frames stop answering under
        # concurrent/rapid requests, so we serialize (and pace) per resolved host. Keyed lazily.
        # Only the one-shot path uses these; the warm pool serializes on its own keeper thread.
        self._locks: dict[str, asyncio.Lock] = {}
        # Kept-warm control sessions (#71). A real Memento frame services a NEW connection only
        # once per ~21s tick, so a session per op can't beat any sane timeout; one warm session per
        # frame, reconnected the instant the frame drops it, makes the same ops sub-second.
        self._pool: WarmSessionPool | None = None
        if settings.frame_warm_sessions and isinstance(self._backend, ConnectedFrameBackend):
            self._pool = WarmSessionPool(self._backend, ports=self._ports, settings=settings)

    async def aclose(self) -> None:
        """Release every warm session (and its keeper thread). Called on app shutdown."""
        if self._pool is not None:
            await self._pool.aclose()

    def session_status(self, host: str) -> str:
        """``live`` | ``connecting`` | ``offline`` | ``idle`` for ``host``'s warm session (#71)."""
        return self._pool.status(host) if self._pool is not None else "idle"

    def _lock_for(self, host: str) -> asyncio.Lock:
        return self._locks.setdefault(host, asyncio.Lock())

    async def discover_frames(
        self, timeout: float = 4.0, *, register: bool = True
    ) -> list[FrameInfo]:
        # register=False just lists what's on the LAN (the onboarding picker) without adding
        # anything; internal auto-pick callers keep register=True.
        frames = await asyncio.to_thread(self._backend.discover, timeout=timeout, ports=self._ports)
        if register and self._store is not None:
            for fi in frames:
                self._register_discovered(fi)
        return frames

    def _register_discovered(self, fi: FrameInfo) -> None:
        """Register/refresh a discovered frame by its **stable id (GUID)**, updating its current
        address; the upsert auto-updates the address so a DHCP change just moves the IP, not the
        identity. The first time we learn a frame's GUID, migrate its old IP-keyed entry (#58)."""
        assert self._store is not None
        frame = Frame.connected(fi.ip, backend=self._backend.name, name=fi.name, guid=fi.guid)
        if frame.id != fi.ip:  # GUID-identified: fold any pre-existing IP-keyed entry into it
            legacy = self._store.get_frame_by_address(fi.ip)
            if legacy is not None and legacy.id == fi.ip:
                self._store.rekey_frame(fi.ip, frame.id)
        self._store.upsert_frame(frame)
        self._store.touch_frame(frame.id)

    def _scan_cidr(self) -> str:
        """The subnet the manual scan probes — explicit config, else a /24 derived from a known
        connected frame or FRAME_HOST. Empty if we can't infer one."""
        if self._settings.frame_scan_cidr:
            return self._settings.frame_scan_cidr
        base = ""
        if self._store is not None:
            base = next(
                (
                    f.address
                    for f in self._store.list_frames()
                    if f.interaction == "connected" and f.address
                ),
                "",
            )
        base = base or self._settings.frame_host
        if base.count(".") == 3:
            return ".".join(base.split(".")[:3]) + ".0/24"
        return ""

    async def scan_for_frames(self, cidr: str | None = None) -> list[FrameInfo]:
        """Actively find connected frames by TCP-probing the control port across the subnet, then
        reading each responder's config. **Discover-only**: returns candidates and registers nothing
        — the user adds one explicitly (``add_frame``). Manual/user-triggered only."""
        cidr = cidr or self._scan_cidr()
        if cidr == "":
            return []
        port = self._ports.control
        hosts = [str(ip) for ip in ipaddress.ip_network(cidr, strict=False).hosts()]
        sem = asyncio.Semaphore(64)

        async def probe(ip: str) -> str | None:
            async with sem:
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip, port), timeout=1.0
                    )
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
                    return ip
                except (TimeoutError, OSError):
                    return None

        live = [ip for ip in await asyncio.gather(*(probe(h) for h in hosts)) if ip]
        candidates: list[FrameInfo] = []
        for ip in live:  # read config (without registering) to describe each candidate
            for _ in range(4):  # the Memento control protocol is flaky; retry the config read
                try:
                    cfg = await self.get_config(ip, register=False)
                except Exception:
                    continue
                candidates.append(_candidate_from_config(ip, cfg))
                break
        return candidates

    async def add_frame(self, host: str) -> Frame | None:
        """Explicitly add a discovered/scanned frame: read its config (which registers it by GUID,
        #51/#58) and return the registered frame."""
        if self._store is None:
            return None
        await self.get_config(host, register=True)
        resolved = await self.resolve_host(host)
        return self._store.get_frame_by_address(resolved) or self._store.get_frame(host)

    def list_known_frames(self) -> list[Frame]:
        """Every frame the registry has seen (across backends). Empty if no registry is attached."""
        return self._store.list_frames() if self._store is not None else []

    async def resolve_host(self, host: str | None = None) -> str:
        if host:
            # ``host`` may be a frame's stable id (GUID) — translate it to the frame's CURRENT
            # address from the registry, so callers never assume a fixed IP (#58).
            if self._store is not None:
                frame = self._store.get_frame(host)
                if frame is not None and frame.address:
                    return frame.address
            return host  # otherwise treat it as a literal IP/host
        if self._settings.frame_host:
            return self._settings.frame_host
        if not self._settings.frame_discovery:
            raise FrameUnavailable("no frame host given and discovery disabled")
        frames = await self.discover_frames()
        if not frames:
            raise FrameUnavailable("no frame found via discovery")
        return frames[0].ip

    async def _with_client(
        self,
        host: str,
        fn: Callable[[FrameConnection], T],
        *,
        register: bool = True,
        quick: bool = False,
        warm: bool = True,
    ) -> T:
        # ``quick`` is the UI read path (current image / a single thumbnail): it never waits for a
        # session to come up and never re-discovers, so a frame that isn't answering reads as
        # offline at once instead of hanging the overview. It also doesn't touch ``last_seen`` —
        # liveness derives from discovery (#66/#68).
        # ``warm`` runs the op on the frame's kept-warm session (#71); pass False for discover-only
        # probes of arbitrary addresses, which are one-shot by nature and shouldn't hold a session.
        resolved = await self.resolve_host(host)
        backend = self._backend
        if not isinstance(backend, ConnectedFrameBackend):
            # Served backends (cloud frames) are driven by the frame polling us, not by us
            # connecting to them — direct per-frame ops land in the served-mounting work (#22).
            raise FrameUnavailable(
                f"backend {backend.name!r} is served (the frame polls us); "
                "direct frame operations aren't available for it"
            )
        pool = self._pool if warm else None

        async def call(address: str) -> T:
            if pool is not None:
                try:
                    return await pool.run(address, fn, quick=quick)
                except WarmSessionUnavailable as exc:
                    # Says which of "offline" / "hasn't answered yet" it is, rather than the old
                    # catch-all "asleep?" that fitted every state and explained none (#71).
                    raise FrameUnavailable(str(exc)) from exc
                except (TimeoutError, OSError) as exc:
                    raise FrameUnavailable(f"frame {address} stopped answering: {exc}") from exc
            timeout = self._settings.frame_quick_timeout if quick else None

            def run() -> T:
                try:
                    with backend.session(address, ports=self._ports, timeout=timeout) as conn:
                        return fn(conn)
                except (TimeoutError, OSError) as exc:
                    # A one-shot connect has to wait out the frame's service tick (~21s, ~42s if it
                    # misses one), so a timeout here says little about the frame's health — hence
                    # the warm pool above. Offline is not a server error: surface it as a clean 503.
                    raise FrameUnavailable(
                        f"frame {address} did not answer its control channel: {exc}"
                    ) from exc

            # Serialize + pace ops to THIS frame so concurrent callers can't overload it. Only the
            # one-shot path needs the lock; the warm pool's keeper thread is already serial.
            async with self._lock_for(address):
                result = await asyncio.to_thread(run)
                if self._settings.frame_settle_delay:
                    await asyncio.sleep(self._settings.frame_settle_delay)
            return result

        try:
            result = await call(resolved)
        except FrameUnavailable:
            # The frame may have moved (DHCP) since we last knew its address. Re-discover and if its
            # address changed, retry once at the new one — so management never assumes a fixed IP
            # (#58). Skipped for discover-only reads and for quick UI reads, which must fail fast.
            if (
                quick
                or not register
                or self._store is None
                or not host
                or not self._settings.frame_discovery
            ):
                raise
            await self.discover_frames()
            new_address = await self.resolve_host(host)
            if new_address == resolved:
                raise
            resolved = new_address
            result = await call(resolved)
        if register and not quick and self._store is not None:  # reached it — record it as seen
            existing = self._store.get_frame_by_address(resolved)
            if existing is not None:  # touch the (GUID) entry, don't make an IP duplicate (#58)
                self._store.touch_frame(existing.id)
            else:
                self._store.upsert_frame(Frame.connected(resolved, backend=backend.name))
                self._store.touch_frame(resolved)
        return result

    def _capture_identity(self, address: str, *, guid: str, name: str) -> None:
        """From a config read, pin the frame's stable GUID identity + reported name (#51/#58).

        Migrates a legacy IP-keyed entry at ``address`` onto its GUID (so curation/delivery survive
        DHCP changes), then captures the name onto that stable id. Establishes GUID identity even
        without working UDP discovery (e.g. a bridged container reaching the frame by host)."""
        if self._store is None:
            return
        current = self._store.get_frame_by_address(address)
        target = guid if guid and guid != NULL_GUID else (current.id if current else address)
        if current is not None and current.id != target:
            self._store.rekey_frame(current.id, target)
        # Upsert with an empty name (preserved by the registry's name CASE) + capture the reported
        # name onto the stable id — capture_name fills placeholders (incl. an IP-shaped one) but
        # never a user rename (#51/#58).
        self._store.upsert_frame(Frame.connected(address, backend=self._backend.name, guid=guid))
        if name:
            self._store.capture_name(target, name)

    # -- config / display -----------------------------------------------------
    async def get_config(self, host: str, *, register: bool = True) -> dict[str, Any]:
        # register=False reads the config WITHOUT adding the frame to the registry — for discovery/
        # scan, which only list candidates; the user adds one explicitly (see add_frame).
        config = await self._with_client(
            host, lambda c: c.get_config(), register=register, warm=register
        )
        if register and self._store is not None:
            self._capture_identity(
                await self.resolve_host(host),
                guid=str(config.get("GUID") or "").strip(),
                name=str(config.get("Name") or "").strip(),
            )
        return config

    async def update_config(self, host: str, patch: dict[str, Any]) -> dict[str, Any]:
        def run(client: FrameConnection) -> dict[str, Any]:
            config = client.get_config()
            config.update(patch)
            client.change_setup(Setup.SendConfig, config)
            return config

        return await self._with_client(host, run)

    async def get_current_image(self, host: str) -> str:
        # A quick UI read: bounded timeout, no re-discovery (the overview polls this).
        return await self._with_client(host, lambda c: c.get_current_image_name(), quick=True)

    async def get_current_thumbnail(self, host: str, *, photos_only: bool = False) -> bytes | None:
        """Quick-read the frame's current image AND its thumbnail in ONE short-timeout session, for
        the cached current-image preview (#68). Returns the thumbnail PNG bytes, or None if the
        frame isn't showing a named image. Fails fast (``FrameUnavailable``) if it can't be reached.

        ``photos_only`` skips Slyde-reserved files (the interlude buffers, #70) so a frame card's
        hero image stays the last real photo instead of flipping to the interlude every other slide.
        """

        def run(client: FrameConnection) -> bytes | None:
            name = client.get_current_image_name()
            if not name or (photos_only and is_reserved_dest(name)):
                return None
            return client.get_thumbnail(name)

        return await self._with_client(host, run, quick=True, register=False)

    async def update_firmware(self, host: str, url: str, md5: str) -> None:
        """Tell the frame to download + apply an update bundle from ``url`` (md5-verified)."""
        await self._with_client(host, lambda c: c.trigger_update(url, md5))

    async def display_image(self, host: str, name: str) -> None:
        """Jump the frame straight to a specific stored image (``Flow.DisplayImage``).

        This is how the interlude conductor drives the rotation (#70). It deliberately does NOT go
        through ``next_image``: the frame computes "next" by looking the *currently displayed*
        filename up in its current album, so from a non-member file (an interlude buffer) that
        lookup misses and the rotation restarts at the first photo. Position is Slyde's state.
        """
        await self._with_client(host, lambda c: c.display_image(name))

    async def set_picture_duration(self, host: str, seconds: int) -> None:
        """Set the frame's own slide time, via the dedicated command (not a whole-config write)."""
        await self._with_client(host, lambda c: c.change_picture_duration(seconds))

    async def set_shuffle(self, host: str, on: bool) -> None:
        """Turn the frame's own shuffle on/off, via the dedicated command."""
        await self._with_client(host, lambda c: c.change_shuffle(on))

    async def next_image(self, host: str) -> None:
        await self._with_client(host, lambda c: c.next_image())

    async def previous_image(self, host: str) -> None:
        await self._with_client(host, lambda c: c.previous_image())

    # -- albums & thumbnails --------------------------------------------------
    async def get_album_data(self, host: str) -> AlbumData:
        return await self._with_client(host, lambda c: c.get_album_data())

    async def get_thumbnails_list(self, host: str) -> list[tuple[str, str]]:
        return await self._with_client(host, lambda c: c.get_thumbnails_list())

    async def get_thumbnail(self, host: str, image_filename: str) -> bytes:
        # A quick UI read (the overview/detail proxies single thumbnails): bounded timeout so a slow
        # frame fails fast instead of blocking on the transfer channel's long default (#68).
        return await self._with_client(host, lambda c: c.get_thumbnail(image_filename), quick=True)

    async def download_image(self, host: str, image_filename: str) -> bytes:
        """Pull the full-resolution original of an on-frame photo (#frame-import)."""
        return await self._with_client(host, lambda c: c.download_image(image_filename))

    async def create_album(self, host: str, name: str) -> AlbumData:
        def run(client: FrameConnection) -> AlbumData:
            data = client.get_album_data()
            data.add_album(name)
            client.send_album_data(data)
            return data

        return await self._with_client(host, run)

    async def delete_album(self, host: str, name: str) -> AlbumData:
        """Delete a (non-reserved) folder from the frame. Photos stay in the library."""

        def run(client: FrameConnection) -> AlbumData:
            data = client.get_album_data()
            data.remove_album(name)
            client.send_album_data(data)
            return data

        return await self._with_client(host, run)

    async def remove_from_album(self, host: str, album: str, filename: str) -> AlbumData:
        """Remove a file from a folder (without deleting the photo from the frame)."""

        def run(client: FrameConnection) -> AlbumData:
            data = client.get_album_data()
            data.remove_image(album, filename)
            client.send_album_data(data)
            return data

        return await self._with_client(host, run)

    async def mirror_album(
        self,
        host: str,
        keep_dests: list[str],
        to_upload: list[tuple[bytes, str]],
        album_name: str,
        on_uploaded: Callable[[str], None] | None = None,
    ) -> list[str]:
        """Upload new images, then set ``album_name`` to exactly ``keep_dests`` + uploaded — a
        1:1 mirror of the source. Returns the dests that uploaded successfully."""

        def run(client: FrameConnection) -> list[str]:
            uploaded: list[str] = []
            for data, dest in to_upload:
                client.upload_image(data, dest)
                uploaded.append(dest)
                if on_uploaded is not None:
                    on_uploaded(dest)
            album_data = client.get_album_data()
            album = album_data.get(album_name) or album_data.add_album(album_name)
            album.images = list(dict.fromkeys(keep_dests + uploaded))
            client.send_album_data(album_data)
            return uploaded

        return await self._with_client(host, run)

    async def delete_photo(self, host: str, filename: str) -> None:
        await self._with_client(host, lambda c: c.delete_image(filename))

    # -- upload (with album assignment) ---------------------------------------
    async def upload_images(
        self,
        host: str,
        items: list[tuple[bytes, str]],
        album: str | None,
        on_uploaded: Callable[[str], None] | None = None,
    ) -> list[str]:
        """Upload (data, dest_name) items; optionally add them to ``album``.

        ``on_uploaded(dest)`` is called after each individual upload succeeds (so callers can
        record durable state only for photos that actually landed). Returns the uploaded dests.
        """

        def run(client: FrameConnection) -> list[str]:
            uploaded: list[str] = []
            for data, dest in items:
                client.upload_image(data, dest)
                uploaded.append(dest)
                if on_uploaded is not None:
                    on_uploaded(dest)
            if album and uploaded:
                album_data = client.get_album_data()
                album_data.add_album(album)
                for dest in uploaded:
                    album_data.add_image(album, dest.lower())
                client.send_album_data(album_data)
            return uploaded

        return await self._with_client(host, run)


async def refresh_current_previews(
    frame_service: FrameService, store: Store, asset_previews: AssetPreviewCache
) -> int:
    """Opportunistically cache each connected frame's current image as that frame's preview (#68).

    A lightweight background pass: for every connected frame, quick-fetch the image it's showing now
    (a short, bounded read that never blocks the render path) and store it under the frame's
    synthetic preview key, so the overview renders it from cached bytes — no live call on the card.
    A frame that won't answer control quickly is skipped; it simply keeps showing the placeholder.
    Returns how many previews were refreshed.
    """
    refreshed = 0
    for frame in store.list_frames():
        if not isinstance(get_backend(frame.backend), ConnectedFrameBackend):
            continue  # only LAN-session frames have a live current image; served (eFrame) and
            # cloud-push (SwitchBot) frames already get a preview from delivered content (#69)
        try:
            png = await frame_service.get_current_thumbnail(frame.id, photos_only=True)
        except FrameUnavailable:
            continue  # offline/slow — leave the existing (or placeholder) preview as-is
        except Exception:
            _log.exception("current-image preview refresh failed for frame %s", frame.id)
            continue
        if not png:
            continue  # frame isn't showing a named image
        try:
            jpeg = await asyncio.to_thread(render_canonical_preview, png)
        except Exception:
            _log.exception("could not render current-image preview for frame %s", frame.id)
            continue
        asset_previews.put(current_preview_key(frame.id), jpeg)
        refreshed += 1
    return refreshed

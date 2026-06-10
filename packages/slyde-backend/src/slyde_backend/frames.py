"""Frame service: an async façade over the synchronous ``memento_core`` client.

Blocking socket operations run in a worker thread so they don't stall the event loop. Operations
are host-parameterized so the app can manage several frames; the host is resolved from an explicit
value, config, or discovery — never hardcoded.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
from collections.abc import Callable
from typing import Any, TypeVar

from memento_core import AlbumData, FrameInfo, Ports, Setup

from .backends import ConnectedFrameBackend, FrameConnection, get_backend
from .config import Settings
from .frame import NULL_GUID, Frame
from .store import Store

T = TypeVar("T")


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

    async def discover_frames(self, timeout: float = 4.0) -> list[FrameInfo]:
        frames = await asyncio.to_thread(self._backend.discover, timeout=timeout, ports=self._ports)
        if self._store is not None:
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

    async def scan_for_frames(self, cidr: str | None = None) -> list[Frame]:
        """Actively find connected frames by TCP-probing the control port across the subnet, then
        reading each responder's config to register it by GUID (#58). Works from a bridged container
        where UDP broadcast discovery can't. Manual/user-triggered only — never run on a timer."""
        cidr = cidr or self._scan_cidr()
        if cidr == "" or self._store is None:
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
        registered: list[Frame] = []
        for ip in live:  # read config -> captures GUID identity + name (#51/#58)
            for _ in range(4):  # the Memento control protocol is flaky; retry the config read
                try:
                    await self.get_config(ip)
                except Exception:
                    continue
                f = self._store.get_frame_by_address(ip)
                if f is not None:
                    registered.append(f)
                break
        return registered

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

    async def _with_client(self, host: str, fn: Callable[[FrameConnection], T]) -> T:
        resolved = await self.resolve_host(host)
        backend = self._backend
        if not isinstance(backend, ConnectedFrameBackend):
            # Served backends (cloud frames) are driven by the frame polling us, not by us
            # connecting to them — direct per-frame ops land in the served-mounting work (#22).
            raise FrameUnavailable(
                f"backend {backend.name!r} is served (the frame polls us); "
                "direct frame operations aren't available for it"
            )

        def run() -> T:
            with backend.session(resolved, ports=self._ports) as conn:
                return fn(conn)

        try:
            result = await asyncio.to_thread(run)
        except FrameUnavailable:
            # The frame may have moved (DHCP) since we last knew its address. Re-discover and if its
            # address changed, retry once at the new one — so management never assumes a fixed IP
            # (#58). ``run`` reads ``resolved`` from this scope, so reassigning it is enough.
            if self._store is None or not host or not self._settings.frame_discovery:
                raise
            await self.discover_frames()
            new_address = await self.resolve_host(host)
            if new_address == resolved:
                raise
            resolved = new_address
            result = await asyncio.to_thread(run)
        if self._store is not None:  # we just reached this frame — record it as seen
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
        keep = current.name if (current and current.name and current.name != current.id) else ""
        self._store.upsert_frame(
            Frame.connected(address, backend=self._backend.name, name=keep, guid=guid)
        )
        if name:
            self._store.capture_name(target, name)

    # -- config / display -----------------------------------------------------
    async def get_config(self, host: str) -> dict[str, Any]:
        config = await self._with_client(host, lambda c: c.get_config())
        if self._store is not None:
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
        return await self._with_client(host, lambda c: c.get_current_image_name())

    async def update_firmware(self, host: str, url: str, md5: str) -> None:
        """Tell the frame to download + apply an update bundle from ``url`` (md5-verified)."""
        await self._with_client(host, lambda c: c.trigger_update(url, md5))

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
        return await self._with_client(host, lambda c: c.get_thumbnail(image_filename))

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

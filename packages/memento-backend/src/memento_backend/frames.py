"""Frame service: an async façade over the synchronous ``memento_core`` client.

Blocking socket operations run in a worker thread so they don't stall the event loop. Operations
are host-parameterized so the app can manage several frames; the host is resolved from an explicit
value, config, or discovery — never hardcoded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from memento_core import AlbumData, FrameClient, FrameInfo, Ports, Setup, discover

from .config import Settings

T = TypeVar("T")


class FrameUnavailable(RuntimeError):
    """Raised when no frame can be resolved or reached."""


class FrameService:
    def __init__(self, settings: Settings, *, ports: Ports | None = None) -> None:
        self._settings = settings
        self._ports = ports or Ports()

    async def discover_frames(self, timeout: float = 4.0) -> list[FrameInfo]:
        return await asyncio.to_thread(discover, timeout=timeout, ports=self._ports)

    async def resolve_host(self, host: str | None = None) -> str:
        if host:
            return host
        if self._settings.frame_host:
            return self._settings.frame_host
        if not self._settings.frame_discovery:
            raise FrameUnavailable("no frame host given and discovery disabled")
        frames = await self.discover_frames()
        if not frames:
            raise FrameUnavailable("no frame found via discovery")
        return frames[0].ip

    async def _with_client(self, host: str, fn: Callable[[FrameClient], T]) -> T:
        resolved = await self.resolve_host(host)

        def run() -> T:
            with FrameClient(resolved, ports=self._ports) as client:
                return fn(client)

        return await asyncio.to_thread(run)

    # -- config / display -----------------------------------------------------
    async def get_config(self, host: str) -> dict[str, Any]:
        return await self._with_client(host, lambda c: c.get_config())

    async def update_config(self, host: str, patch: dict[str, Any]) -> dict[str, Any]:
        def run(client: FrameClient) -> dict[str, Any]:
            config = client.get_config()
            config.update(patch)
            client.change_setup(Setup.SendConfig, config)
            return config

        return await self._with_client(host, run)

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
        def run(client: FrameClient) -> AlbumData:
            data = client.get_album_data()
            data.add_album(name)
            client.send_album_data(data)
            return data

        return await self._with_client(host, run)

    async def delete_album(self, host: str, name: str) -> AlbumData:
        """Delete a (non-reserved) folder from the frame. Photos stay in the library."""
        def run(client: FrameClient) -> AlbumData:
            data = client.get_album_data()
            data.remove_album(name)
            client.send_album_data(data)
            return data

        return await self._with_client(host, run)

    async def remove_from_album(self, host: str, album: str, filename: str) -> AlbumData:
        """Remove a file from a folder (without deleting the photo from the frame)."""
        def run(client: FrameClient) -> AlbumData:
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

        def run(client: FrameClient) -> list[str]:
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

        def run(client: FrameClient) -> list[str]:
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

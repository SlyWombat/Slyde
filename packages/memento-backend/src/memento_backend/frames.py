"""Frame service: an async façade over the synchronous ``memento_core`` client.

Blocking socket operations run in a worker thread so they don't stall the event loop. The frame
target is resolved from config (explicit host) or discovery — never hardcoded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from memento_core import FrameClient, Ports, Setup, discover

from .config import Settings

T = TypeVar("T")


class FrameUnavailable(RuntimeError):
    """Raised when no frame can be resolved or reached."""


class FrameService:
    def __init__(self, settings: Settings, *, ports: Ports | None = None) -> None:
        self._settings = settings
        self._ports = ports or Ports()

    async def resolve_host(self) -> str:
        if self._settings.frame_host:
            return self._settings.frame_host
        if not self._settings.frame_discovery:
            raise FrameUnavailable("no FRAME_HOST set and discovery disabled")
        frames = await asyncio.to_thread(discover, timeout=4.0, ports=self._ports)
        if not frames:
            raise FrameUnavailable("no frame found via discovery")
        return frames[0].ip

    async def _with_client(self, fn: Callable[[FrameClient], T]) -> T:
        host = await self.resolve_host()

        def run() -> T:
            with FrameClient(host, ports=self._ports) as client:
                return fn(client)

        return await asyncio.to_thread(run)

    async def get_config(self) -> dict[str, Any]:
        return await self._with_client(lambda c: c.get_config())

    async def get_frame_time(self) -> dict[str, Any]:
        return await self._with_client(lambda c: c.get_frame_time())

    async def set_config(self, action: Setup, payload: dict[str, Any]) -> None:
        await self._with_client(lambda c: c.change_setup(action, payload))

    async def update_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge ``patch`` into the live config and push it back (verified GetConfig→SendConfig)."""

        def run(client: FrameClient) -> dict[str, Any]:
            config = client.get_config()
            config.update(patch)
            client.change_setup(Setup.SendConfig, config)
            return config

        return await self._with_client(run)

    async def upload(self, data: bytes, dest_name: str) -> None:
        await self._with_client(lambda c: c.upload_image(data, dest_name))

    async def delete(self, dest_name: str) -> None:
        await self._with_client(lambda c: c.delete_image(dest_name))

    async def next_image(self) -> None:
        await self._with_client(lambda c: c.next_image())

    async def previous_image(self) -> None:
        await self._with_client(lambda c: c.previous_image())

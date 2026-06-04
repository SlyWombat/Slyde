"""Firmware/app update registry + artifact server (modelled on SlyLED's OTA).

A registry pins a target version + bundle URL + md5 per device track. The manager serves the
bundle (verifying the md5 before each serve, like SlyLED's otaSha256 guard) and tells a frame to
pull it via the protocol's ``TriggerUpdate(url, md5)``. ``check()`` refreshes the registry from the
configured GitHub repo's latest release.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


class FirmwareError(RuntimeError):
    """Unknown track, missing asset, or a failed integrity check."""


@dataclass
class FirmwareTrack:
    track: str
    version: str
    url: str
    md5: str


class FirmwareService:
    def __init__(
        self,
        settings: Settings,
        *,
        fetch: Callable[[str], Awaitable[bytes]] | None = None,
        release_fetch: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._settings = settings
        self._fetch = fetch
        self._release_fetch = release_fetch
        self._registry: dict[str, FirmwareTrack] = {}
        self._cache: dict[str, bytes] = {}

    def tracks(self) -> list[FirmwareTrack]:
        return list(self._registry.values())

    def get(self, track: str) -> FirmwareTrack | None:
        return self._registry.get(track)

    async def check(self) -> list[FirmwareTrack]:
        """Refresh the registry from the configured repo's latest GitHub release."""
        release = await self._latest_release()
        raw = str(release.get("tag_name") or release.get("name") or "")
        match = re.search(r"\d+(?:\.\d+)*", raw)  # e.g. "softframe-v1.2.3" -> "1.2.3"
        version = match.group(0) if match else raw
        assets = {str(a["name"]): str(a["browser_download_url"]) for a in release.get("assets", [])}
        track = self._settings.firmware_track
        zip_name = next((n for n in assets if n.startswith(track) and n.endswith(".zip")), None)
        if zip_name is None:
            raise FirmwareError(f"no '{track}*.zip' asset in the latest release")
        md5 = ""
        if (sidecar := f"{zip_name}.md5") in assets:
            md5 = (await self._fetch_bytes(assets[sidecar])).decode("utf-8", "replace").split()[0]
        self._registry[track] = FirmwareTrack(track, version, assets[zip_name], md5.strip())
        self._cache.pop(track, None)
        return self.tracks()

    async def serve(self, track: str) -> bytes:
        entry = self._registry.get(track)
        if entry is None:
            raise FirmwareError(f"unknown firmware track: {track}")
        data = self._cache.get(track)
        if data is None:
            data = await self._fetch_bytes(entry.url)
        if entry.md5 and hashlib.md5(data).hexdigest() != entry.md5.lower():
            raise FirmwareError("artifact md5 mismatch — refusing to serve a corrupt update")
        self._cache[track] = data
        return data

    # -- default network fetchers (overridable for tests) ---------------------
    async def _latest_release(self) -> dict[str, Any]:
        if self._release_fetch is not None:
            return await self._release_fetch()
        if not self._settings.firmware_repo:
            raise FirmwareError("FIRMWARE_REPO is not configured")
        url = f"https://api.github.com/repos/{self._settings.firmware_repo}/releases/latest"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code >= 400:
            raise FirmwareError(f"GitHub releases {resp.status_code}: {resp.text[:200]}")
        return dict(resp.json())

    async def _fetch_bytes(self, url: str) -> bytes:
        if self._fetch is not None:
            return await self._fetch(url)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            raise FirmwareError(f"fetch {url} -> {resp.status_code}")
        return resp.content

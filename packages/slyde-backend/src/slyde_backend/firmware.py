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
        assets = {str(a["name"]): self._asset_url(a) for a in release.get("assets", [])}
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

    def _asset_url(self, asset: dict[str, Any]) -> str:
        """Pick how to download a release asset.

        Public/tokenless repos use the ``browser_download_url`` — the CDN download that needs no
        auth and (unlike the API asset URL) doesn't briefly 404 while a just-uploaded asset settles.
        Private repos (a token is set) use the API asset URL, which honours the bearer token.
        """
        api_url = asset.get("url")
        if self._settings.firmware_github_token and api_url:
            return str(api_url)
        return str(asset.get("browser_download_url") or api_url)

    # -- default network fetchers (overridable for tests) ---------------------
    def _auth_headers(self) -> dict[str, str]:
        token = self._settings.firmware_github_token
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _latest_release(self) -> dict[str, Any]:
        if self._release_fetch is not None:
            return await self._release_fetch()
        if not self._settings.firmware_repo:
            raise FirmwareError("FIRMWARE_REPO is not configured")
        url = f"https://api.github.com/repos/{self._settings.firmware_repo}/releases/latest"
        headers = {"Accept": "application/vnd.github+json", **self._auth_headers()}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise FirmwareError(f"GitHub releases {resp.status_code}: {resp.text[:200]}")
        return dict(resp.json())

    async def _fetch_bytes(self, url: str) -> bytes:
        if self._fetch is not None:
            return await self._fetch(url)
        # octet-stream makes the API asset URL return the binary (not JSON metadata).
        headers = {"Accept": "application/octet-stream", **self._auth_headers()}
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise FirmwareError(f"fetch {url} -> {resp.status_code}")
        return resp.content

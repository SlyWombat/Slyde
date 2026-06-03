"""Minimal async Immich REST client.

Targets the documented Immich API (auth via the ``x-api-key`` header). Endpoints are kept in one
place so they are easy to adjust for a given Immich version; behaviour is covered by mocked tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class ImmichAlbum:
    id: str
    name: str
    asset_count: int


@dataclass
class ImmichAsset:
    id: str
    file_name: str
    type: str  # "IMAGE" | "VIDEO"


class ImmichError(RuntimeError):
    """Raised on a non-success response from Immich."""


class ImmichClient:
    """Async client for an Immich instance. Construct with the base URL and API key."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url or not api_key:
            raise ValueError("Immich base URL and API key are required")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ImmichClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get(self, path: str, **kwargs: object) -> httpx.Response:
        resp = await self._client.get(path, **kwargs)  # type: ignore[arg-type]
        if resp.status_code >= 400:
            raise ImmichError(f"Immich GET {path} -> {resp.status_code}: {resp.text[:200]}")
        return resp

    async def list_albums(self) -> list[ImmichAlbum]:
        data = (await self._get("/api/albums")).json()
        return [
            ImmichAlbum(
                id=a["id"],
                name=a.get("albumName", a.get("name", "")),
                asset_count=int(a.get("assetCount", 0)),
            )
            for a in data
        ]

    async def album_assets(self, album_id: str) -> list[ImmichAsset]:
        data = (await self._get(f"/api/albums/{album_id}")).json()
        return [
            ImmichAsset(
                id=a["id"],
                file_name=a.get("originalFileName", a.get("id", "")),
                type=a.get("type", "IMAGE"),
            )
            for a in data.get("assets", [])
        ]

    async def get_asset(self, asset_id: str) -> ImmichAsset:
        a = (await self._get(f"/api/assets/{asset_id}")).json()
        return ImmichAsset(
            id=a["id"],
            file_name=a.get("originalFileName", a.get("id", "")),
            type=a.get("type", "IMAGE"),
        )

    async def asset_bytes(self, asset_id: str, size: str = "preview") -> bytes:
        """Fetch image bytes for an asset. ``size`` is an Immich thumbnail size or ``original``."""
        if size == "original":
            resp = await self._get(f"/api/assets/{asset_id}/original")
        else:
            resp = await self._get(f"/api/assets/{asset_id}/thumbnail", params={"size": size})
        return resp.content

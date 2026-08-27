"""The write side of the Immich integration — deliberately NOT part of ``ImmichClient`` (#72).

``ImmichClient`` is read-only by contract, enforced by
``tests/test_immich.py::test_immich_client_is_read_only``: everything on Slyde's automatic path
(curation, delivery, previews) can only ever read from Immich, so no scheduler bug can write to the
user's library. Importing a frame's existing photos genuinely needs to create assets and albums, so
that capability lives in a separate class that nothing on the automatic path constructs. The
read-only guarantee keeps its original meaning where it matters; only a deliberate, user-triggered
import can write, and the blast radius of a mistake here is one import job.

Targets the Immich v3 API (verified against v3.0.1): ``POST /api/assets`` is multipart and requires
``fileCreatedAt``/``fileModifiedAt``; ``PUT /api/albums/{id}/assets`` takes ``{"ids": [...]}`` and
reports per-asset success so an already-present asset is a no-op rather than an error.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import httpx

from .immich import ImmichError

# Immich rejects very large batches; assets are added to an album in chunks of this size.
_ALBUM_BATCH = 500


@dataclass(frozen=True)
class UploadedAsset:
    """The asset Immich now holds. ``duplicate`` when Immich matched an existing one by checksum."""

    id: str
    duplicate: bool


class ImmichWriter:
    """Creates assets and albums in Immich. Construct only from an explicit import path."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 120.0,  # uploads carry whole photos, not JSON
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

    async def __aenter__(self) -> ImmichWriter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    @staticmethod
    def _ok(resp: httpx.Response, what: str) -> httpx.Response:
        if resp.status_code >= 400:
            raise ImmichError(f"{what} failed: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    async def upload_asset(
        self,
        data: bytes,
        *,
        filename: str,
        device_asset_id: str,
        device_id: str,
        created_at: datetime,
        modified_at: datetime | None = None,
    ) -> UploadedAsset:
        """Upload one photo. Immich dedupes by checksum, returning the existing asset's id.

        That dedupe is what makes a re-run of an interrupted import safe: an already-uploaded photo
        comes back as ``duplicate`` with its id, so it still gets added to the right albums.
        """
        stamp = (modified_at or created_at).isoformat()
        resp = await self._client.post(
            "/api/assets",
            files={"assetData": (filename, data)},
            data={
                "deviceAssetId": device_asset_id,
                "deviceId": device_id,
                "fileCreatedAt": created_at.isoformat(),
                "fileModifiedAt": stamp,
            },
        )
        payload = self._ok(resp, f"upload {filename!r}").json()
        asset_id = str(payload.get("id") or "")
        if not asset_id:
            raise ImmichError(f"upload {filename!r} returned no asset id: {payload}")
        return UploadedAsset(asset_id, str(payload.get("status", "")).lower() == "duplicate")

    async def find_assets_by_filename(self, filename: str) -> list[str]:
        """Asset ids whose original filename is ``filename``. A read — but a POST, which is why it
        lives here rather than on the GET-only ``ImmichClient``.

        Used to link a frame's albums to photos already in the library, instead of importing the
        frame's panel-sized re-encodes of them (#72).
        """
        resp = self._ok(
            await self._client.post("/api/search/metadata", json={"originalFileName": filename}),
            f"search for {filename!r}",
        )
        items = (resp.json().get("assets") or {}).get("items") or []
        wanted = filename.casefold()
        # The search is a contains/fuzzy match on some versions, so keep only exact filenames —
        # linking the wrong photo into someone's album is worse than leaving a gap.
        return [
            str(a["id"])
            for a in items
            if str(a.get("originalFileName", "")).casefold() == wanted and a.get("id")
        ]

    async def find_album(self, name: str) -> str | None:
        resp = self._ok(await self._client.get("/api/albums"), "list albums")
        for album in resp.json():
            if str(album.get("albumName", "")) == name:
                return str(album.get("id"))
        return None

    async def ensure_album(self, name: str, *, description: str = "") -> tuple[str, bool]:
        """Find the album called ``name``, or create it. Returns ``(id, created)``.

        Matching by name is what keeps a repeated import from growing a second "Memento - Mexico"
        every run.
        """
        existing = await self.find_album(name)
        if existing is not None:
            return existing, False
        resp = self._ok(
            await self._client.post(
                "/api/albums", json={"albumName": name, "description": description}
            ),
            f"create album {name!r}",
        )
        album_id = str(resp.json().get("id") or "")
        if not album_id:
            raise ImmichError(f"create album {name!r} returned no id")
        return album_id, True

    async def add_assets(self, album_id: str, asset_ids: Iterable[str]) -> int:
        """Add assets to an album, in batches. Returns how many were newly added.

        Assets already in the album come back as a per-item failure, not an HTTP error — they're
        counted as "not newly added" rather than raised, so re-running an import is a no-op.
        """
        ids = list(dict.fromkeys(asset_ids))  # de-dup, preserve order
        added = 0
        for start in range(0, len(ids), _ALBUM_BATCH):
            batch = ids[start : start + _ALBUM_BATCH]
            resp = self._ok(
                await self._client.put(f"/api/albums/{album_id}/assets", json={"ids": batch}),
                f"add {len(batch)} assets to album",
            )
            results = resp.json()
            if isinstance(results, list):
                added += sum(1 for r in results if r.get("success"))
        return added

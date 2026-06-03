"""Immich client against a mocked transport."""

from __future__ import annotations

import asyncio

import httpx

from memento_backend.immich import ImmichClient


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("x-api-key") == "secret"
    path = request.url.path
    if path == "/api/albums":
        return httpx.Response(200, json=[{"id": "a1", "albumName": "Trips", "assetCount": 2}])
    if path == "/api/albums/a1":
        return httpx.Response(
            200,
            json={
                "id": "a1",
                "assets": [
                    {"id": "x1", "originalFileName": "beach.jpg", "type": "IMAGE"},
                    {"id": "x2", "originalFileName": "clip.mov", "type": "VIDEO"},
                ],
            },
        )
    if path == "/api/assets/x1/thumbnail":
        return httpx.Response(200, content=b"JPEGBYTES")
    return httpx.Response(404)


def _client() -> ImmichClient:
    return ImmichClient("http://immich.test", "secret", transport=httpx.MockTransport(_handler))


def test_list_albums() -> None:
    async def run() -> None:
        async with _client() as c:
            albums = await c.list_albums()
        assert len(albums) == 1
        assert albums[0].name == "Trips" and albums[0].asset_count == 2

    asyncio.run(run())


def test_album_assets_and_bytes() -> None:
    async def run() -> None:
        async with _client() as c:
            assets = await c.album_assets("a1")
            data = await c.asset_bytes("x1", "preview")
        assert [a.type for a in assets] == ["IMAGE", "VIDEO"]
        assert data == b"JPEGBYTES"

    asyncio.run(run())

"""Immich client against a mocked transport."""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx

from slyde_backend.immich import ImmichClient, ImmichError


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
    if path == "/api/albums/a3":  # Immich 3: no asset list, only the count
        return httpx.Response(200, json={"id": "a3", "assetCount": 3, "order": "asc"})
    if path == "/api/search/metadata":
        body = json.loads(request.content)
        assert body["albumIds"] == ["a3"] and body["order"] == "asc"
        pages = {
            1: {
                "items": [{"id": "y1", "originalFileName": "a.jpg", "type": "IMAGE"}] * 2,
                "nextPage": "2",
            },
            2: {
                "items": [{"id": "y3", "originalFileName": "c.mov", "type": "VIDEO"}],
                "nextPage": None,
            },
        }
        return httpx.Response(200, json={"assets": pages[body["page"]]})
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


def test_album_assets_on_immich_3_come_from_a_paged_search() -> None:
    async def run() -> None:
        async with _client() as c:
            assets = await c.album_assets("a3")
        assert [(a.id, a.type) for a in assets] == [
            ("y1", "IMAGE"),
            ("y1", "IMAGE"),
            ("y3", "VIDEO"),
        ]

    asyncio.run(run())


def test_immich_client_is_read_only() -> None:
    """Guarantee (README "Read-only & one-way"): the client NEVER mutates Immich.

    Exercise every public method and assert every HTTP request it issues is a GET —
    no POST/PUT/PATCH/DELETE ever reaches Immich — except the metadata search, a query that
    Immich only accepts as a POST. This is the enforcement behind the
    one-way read-only contract; if someone adds a mutating call, this test fails.
    """
    methods_seen: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        if not (request.method == "POST" and request.url.path == "/api/search/metadata"):
            methods_seen.append(request.method)
        return _handler(request)

    async def run() -> None:
        client = ImmichClient(
            "http://immich.test", "secret", transport=httpx.MockTransport(recording)
        )
        async with client as c:
            await c.list_albums()
            await c.album_assets("a1")
            await c.album_assets("a3")  # Immich 3 path: the read-only search
            await c.asset_bytes("x1", "preview")
            # get_asset / original paths aren't mocked (the 404 -> ImmichError is fine);
            # we only care that the verb issued was still a GET.
            with contextlib.suppress(ImmichError):
                await c.get_asset("x1")
            with contextlib.suppress(ImmichError):
                await c.asset_bytes("x1", "original")

    asyncio.run(run())
    assert methods_seen, "no requests were recorded"
    assert set(methods_seen) == {"GET"}, f"non-read HTTP verb issued to Immich: {set(methods_seen)}"

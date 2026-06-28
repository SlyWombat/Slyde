"""SwitchBot AI Art Frame OpenAPI client against a mocked transport (#64)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest

from slyde_backend.switchbot import ART_FRAME, SwitchBotClient, SwitchBotError

_posted: list[dict] = []


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json={"statusCode": 100, "body": body, "message": "success"})


def _handler(request: httpx.Request) -> httpx.Response:
    for h in ("Authorization", "sign", "t", "nonce"):  # every request must be signed
        assert request.headers.get(h), f"missing {h} header"
    path = request.url.path
    if path == "/v1.1/devices":
        return _ok(
            {
                "deviceList": [
                    {
                        "deviceId": "F1",
                        "deviceType": ART_FRAME,
                        "deviceName": "Hallway",
                        "hubDeviceId": "H1",
                    },
                    {"deviceId": "B1", "deviceType": "Bot", "deviceName": "Kettle"},
                ]
            }
        )
    if path == "/v1.1/devices/F1/status":
        return _ok(
            {
                "deviceId": "F1",
                "deviceType": ART_FRAME,
                "battery": 87,
                "displayMode": 1,
                "imageUrl": "https://img/cur.jpg",
                "version": "V0.0-0.5",
            }
        )
    if path == "/v1.1/devices/F1/commands":
        _posted.append(json.loads(request.content))
        return _ok({})
    if path == "/v1.1/devices/ERR/status":
        return httpx.Response(200, json={"statusCode": 161, "message": "device offline"})
    return httpx.Response(404)


def _client() -> SwitchBotClient:
    return SwitchBotClient("TOK", "SEC", transport=httpx.MockTransport(_handler))


def test_sign_headers_match_the_documented_hmac() -> None:
    async def run() -> dict[str, str]:
        async with _client() as c:
            return c._sign_headers(now_ms=1700000000000, nonce="NONCE")

    h = asyncio.run(run())
    expected = (
        base64.b64encode(hmac.new(b"SEC", b"TOK1700000000000NONCE", hashlib.sha256).digest())
        .decode()
        .upper()
    )
    assert h["Authorization"] == "TOK" and h["t"] == "1700000000000" and h["nonce"] == "NONCE"
    assert h["sign"] == expected  # base64(HMAC-SHA256(secret, token+t+nonce)).upper()


def test_art_frames_filters_by_device_type() -> None:
    async def run() -> list:
        async with _client() as c:
            return await c.art_frames()

    frames = asyncio.run(run())
    assert [f.device_id for f in frames] == ["F1"]  # the Bot is filtered out
    assert frames[0].name == "Hallway" and frames[0].hub_id == "H1"


def test_art_frame_status_parses_fields() -> None:
    async def run():  # type: ignore[no-untyped-def]
        async with _client() as c:
            return await c.art_frame_status("F1")

    s = asyncio.run(run())
    assert s.battery == 87 and s.display_mode == 1
    assert s.image_url == "https://img/cur.jpg" and s.version == "V0.0-0.5"


def test_commands_send_the_documented_payloads() -> None:
    _posted.clear()

    async def run() -> None:
        async with _client() as c:
            await c.next_image("F1")
            await c.previous_image("F1")
            await c.upload_image_url("F1", "https://img/new.jpg")
            await c.upload_image_bytes("F1", b"\xff\xd8\xffJPEG\xff\xd9")

    asyncio.run(run())
    assert _posted[0] == {"commandType": "command", "command": "next", "parameter": "default"}
    assert _posted[1]["command"] == "previous"
    assert _posted[2] == {
        "commandType": "command",
        "command": "uploadImage",
        "parameter": {"imageUrl": "https://img/new.jpg"},
    }
    b64 = base64.b64encode(b"\xff\xd8\xffJPEG\xff\xd9").decode()
    assert _posted[3]["parameter"] == {"imageBase64": f"data:image/jpeg;base64,{b64}"}


def test_non_ok_status_code_raises() -> None:
    async def run() -> None:
        async with _client() as c:
            await c.art_frame_status("ERR")

    with pytest.raises(SwitchBotError):
        asyncio.run(run())

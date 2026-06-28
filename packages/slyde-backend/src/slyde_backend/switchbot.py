"""SwitchBot OpenAPI v1.1 client — control + photo delivery for the AI Art Frame (#64).

Unlike Memento (reverse-engineered LAN) and the Sungale eFrame (cloud impersonation), the SwitchBot
AI Art Frame is driven entirely through SwitchBot's **official, signed cloud API**: list devices,
read status (battery / display mode / current image), **push a photo** (``uploadImage`` — an image
URL or a base64 JPEG), and next/previous. So Slyde *pushes* via the vendor cloud — no DNS redirect,
no reverse-engineering. This is a standalone control/delivery client; the ``switchbot`` FrameBackend
that wires it into the curation/delivery queue lands when the hardware is in hand.

Auth (per the API docs): each request carries ``Authorization`` (token), ``t`` (13-digit ms epoch),
``nonce`` (UUID), and ``sign`` = base64(HMAC-SHA256(secret, token + t + nonce)).upper(). The API
wraps every response as ``{"statusCode": 100, "body": {...}, "message": "success"}``; 100 = OK.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

_BASE_URL = "https://api.switch-bot.com"
ART_FRAME = "AI Art Frame"  # the device's deviceType string
_OK = 100  # SwitchBot's success statusCode


class SwitchBotError(RuntimeError):
    """A SwitchBot API call failed (transport error or non-100 statusCode)."""


@dataclass(frozen=True)
class SwitchBotDevice:
    device_id: str
    device_type: str
    name: str = ""
    hub_id: str = ""


@dataclass(frozen=True)
class ArtFrameStatus:
    device_id: str
    battery: int  # 0-100
    display_mode: int  # 0 = static image, 1 = slideshow
    image_url: str  # the image currently displayed
    version: str  # firmware, e.g. "V0.0-0.5"


class SwitchBotClient:
    def __init__(
        self,
        token: str,
        secret: str,
        *,
        base_url: str = _BASE_URL,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token or not secret:
            raise ValueError("SwitchBot token and secret are required")
        self._token = token
        self._secret = secret.encode()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> SwitchBotClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _sign_headers(
        self, *, now_ms: int | None = None, nonce: str | None = None
    ) -> dict[str, str]:
        """Build the signed auth headers (``now_ms``/``nonce`` injectable for tests)."""
        t = str(now_ms if now_ms is not None else int(time.time() * 1000))
        n = nonce or str(uuid.uuid4())
        digest = hmac.new(self._secret, f"{self._token}{t}{n}".encode(), hashlib.sha256).digest()
        sign = base64.b64encode(digest).decode().upper()
        return {
            "Authorization": self._token,
            "sign": sign,
            "t": t,
            "nonce": n,
            "Content-Type": "application/json; charset=utf8",
        }

    @staticmethod
    def _unwrap(resp: httpx.Response, where: str) -> dict[str, Any]:
        try:
            data = resp.json()
        except Exception as exc:  # non-JSON (gateway error, etc.)
            raise SwitchBotError(f"{where}: non-JSON response ({resp.status_code})") from exc
        if resp.status_code != 200 or data.get("statusCode") != _OK:
            raise SwitchBotError(
                f"{where}: statusCode={data.get('statusCode')} message={data.get('message')!r}"
            )
        body = data.get("body")
        return body if isinstance(body, dict) else {}

    async def _get(self, path: str) -> dict[str, Any]:
        resp = await self._client.get(path, headers=self._sign_headers())
        return self._unwrap(resp, f"GET {path}")

    async def _command(self, device_id: str, command: str, parameter: object = "default") -> None:
        path = f"/v1.1/devices/{device_id}/commands"
        payload = {"commandType": "command", "command": command, "parameter": parameter}
        resp = await self._client.post(path, headers=self._sign_headers(), json=payload)
        self._unwrap(resp, f"POST {command}")

    # -- discovery / status ---------------------------------------------------
    async def list_devices(self) -> list[SwitchBotDevice]:
        body = await self._get("/v1.1/devices")
        return [
            SwitchBotDevice(
                device_id=d["deviceId"],
                device_type=d.get("deviceType", ""),
                name=d.get("deviceName", ""),
                hub_id=d.get("hubDeviceId", ""),
            )
            for d in body.get("deviceList", [])
        ]

    async def art_frames(self) -> list[SwitchBotDevice]:
        """Just the AI Art Frames on the account."""
        return [d for d in await self.list_devices() if d.device_type == ART_FRAME]

    async def art_frame_status(self, device_id: str) -> ArtFrameStatus:
        b = await self._get(f"/v1.1/devices/{device_id}/status")
        return ArtFrameStatus(
            device_id=device_id,
            battery=int(b.get("battery", 0)),
            display_mode=int(b.get("displayMode", 0)),
            image_url=str(b.get("imageUrl", "")),
            version=str(b.get("version", "")),
        )

    # -- control / delivery ---------------------------------------------------
    async def next_image(self, device_id: str) -> None:
        await self._command(device_id, "next")

    async def previous_image(self, device_id: str) -> None:
        await self._command(device_id, "previous")

    async def upload_image_url(self, device_id: str, image_url: str) -> None:
        """Display an image the frame fetches from ``image_url`` (e.g. a Slyde-served JPEG)."""
        await self._command(device_id, "uploadImage", {"imageUrl": image_url})

    async def upload_image_bytes(
        self, device_id: str, image: bytes, *, mime: str = "image/jpeg"
    ) -> None:
        """Display ``image`` by sending it inline as a base64 data URI."""
        b64 = base64.b64encode(image).decode()
        await self._command(device_id, "uploadImage", {"imageBase64": f"data:{mime};base64,{b64}"})

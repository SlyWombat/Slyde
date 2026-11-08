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

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

_BASE_URL = "https://api.switch-bot.com"
ART_FRAME = "AI Art Frame"  # the device's deviceType string
_OK = 100  # SwitchBot's success statusCode


class SwitchBotError(RuntimeError):
    """A SwitchBot API call failed (transport error or non-100 statusCode)."""


class SwitchBotRejectedError(SwitchBotError):
    """The call was refused for a reason a retry won't fix (e.g. the image is invalid)."""


@dataclass(frozen=True)
class SwitchBotDevice:
    device_id: str
    device_type: str
    name: str = ""
    hub_id: str = ""


@dataclass(frozen=True)
class ArtFrameStatus:
    device_id: str
    battery: int | None  # 0-100; None when the transport can't read it (HA bridge)
    display_mode: int | None  # 0 = static image, 1 = slideshow; None when unknown
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


# The ha-switchbot-art-frame action domain, and HA entity ids we accept for its local_images sensor
# (validated up front: the id is interpolated into REST paths).
_HA_DOMAIN = "switchbot_art_frame"
HA_ENTITY_ID_RE = re.compile(r"^sensor\.[a-z0-9_]+$")
# HA WebSocket error codes a retry can't fix: a malformed call, an unknown entity, or the action's
# own ServiceValidationError (image too large / not a JPEG, no SwitchBot account in the
# integration). A HomeAssistantError (frame asleep, BLE or cloud hiccup) stays retryable.
_HA_PERMANENT_ERRORS = frozenset({"invalid_format", "not_found", "service_validation_error"})


class _WebSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...


WebSocketConnect = Callable[[str], AbstractAsyncContextManager[_WebSocket]]


def _default_ws_connect(url: str) -> AbstractAsyncContextManager[_WebSocket]:
    # A show_image call carries the ~0.9 MB base64 JPEG; replies are small. HA (and the Supervisor
    # proxy) accept messages up to 4 MiB.
    return ws_connect(url, max_size=4 * 1024 * 1024, open_timeout=15)


def ha_websocket_url(base_url: str) -> str:
    """Home Assistant's WebSocket endpoint for ``base_url``: ``/api/websocket`` on a Home Assistant
    host, or the Supervisor's ``/core/websocket`` proxy inside an add-on (``http://supervisor/core``).
    """
    url = httpx.URL(base_url.rstrip("/"))
    scheme = "wss" if url.scheme == "https" else "ws"
    path = url.path.rstrip("/")
    if url.host == "supervisor" and path.endswith("/core"):
        path += "/websocket"
    else:
        path += "/api/websocket"
    return str(url.copy_with(scheme=scheme, path=path))


class HomeAssistantArtFrameClient:
    """Drives a SwitchBot AI Art Frame through Home Assistant's ``ha-switchbot-art-frame``
    integration (``switchbot_art_frame.show_image``).

    Why this bridge exists: SwitchBot's public OpenAPI ``uploadImage`` cannot delete images from
    the frame's 10 local slots or its 10-slot cloud album, nor can it wake the frame over BLE to
    pull the upload immediately. ``ha-switchbot-art-frame`` combines SwitchBot Cloud, the private
    app album API, and Home Assistant's Bluetooth (including ESPHome Bluetooth Proxies) into one
    ``show_image(make_room=True)`` action that evicts the oldest slot when full, uploads the new
    JPEG, wakes the frame over BLE, and displays it.

    The action is called over HA's WebSocket API, not ``POST /api/services``: the REST endpoint
    stops waiting after 10 s and answers 200 while the action keeps running, but ``show_image``
    takes 30-90 s — so REST would report every later failure as success and the photo would be
    marked delivered. Over WebSocket we wait for the action's real outcome (HA itself runs the
    integration's actions one at a time per frame).

    Supports both standalone Docker (``HA_BASE_URL`` + ``HA_TOKEN``) and Home Assistant Add-on mode
    (``http://supervisor/core`` + ``SUPERVISOR_TOKEN``).
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        entity_id: str,
        *,
        device_id: str = "",
        timeout: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
        websocket_connect: WebSocketConnect | None = None,
        show_on_upload: bool = True,
    ) -> None:
        if not base_url or not token or not entity_id:
            raise ValueError("Home Assistant base_url, token, and entity_id are required")
        if not HA_ENTITY_ID_RE.match(entity_id):
            raise ValueError(
                f"{entity_id!r} is not a sensor entity id (expected e.g. "
                "sensor.art_frame_local_images)"
            )
        self._entity_id = entity_id
        self._device_id = device_id or entity_id
        self._token = token
        self._timeout = timeout
        self._ws_url = ha_websocket_url(base_url)
        self._ws_connect = websocket_connect or _default_ws_connect
        self._show_on_upload = show_on_upload
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=30.0,
            transport=transport,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HomeAssistantArtFrameClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get_state(self) -> dict[str, Any]:
        path = f"/api/states/{self._entity_id}"
        resp = await self._client.get(path)
        if resp.status_code != 200:
            raise SwitchBotError(f"GET {path}: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise SwitchBotError(f"GET {path}: non-JSON response") from exc
        if not isinstance(data, dict):
            raise SwitchBotError(f"GET {path}: unexpected payload")
        return data

    async def _call_service(self, service: str, data: dict[str, Any]) -> Any:
        """Run ``switchbot_art_frame.<service>`` on our entity and wait for its real outcome.

        Raises ``SwitchBotRejectedError`` when HA refuses the call for good, ``SwitchBotError`` for
        anything a retry may fix (auth hiccup, connection lost, frame unreachable, timeout).
        """
        call = {
            "id": 1,
            "type": "call_service",
            "domain": _HA_DOMAIN,
            "service": service,
            "service_data": data,
            "target": {"entity_id": self._entity_id},
            "return_response": True,
        }
        try:
            async with asyncio.timeout(self._timeout), self._ws_connect(self._ws_url) as ws:
                hello = json.loads(await ws.recv())
                if hello.get("type") == "auth_required":
                    await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
                    auth = json.loads(await ws.recv())
                    if auth.get("type") != "auth_ok":
                        raise SwitchBotError(f"Home Assistant WebSocket auth failed: {auth}")
                await ws.send(json.dumps(call))
                while True:  # skip anything that isn't our result (there are no subscriptions)
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == 1 and msg.get("type") == "result":
                        break
        except TimeoutError as exc:
            raise SwitchBotError(
                f"{service}: no answer from Home Assistant in {self._timeout:.0f}s"
            ) from exc
        except (WebSocketException, ValueError) as exc:
            raise SwitchBotError(f"{service}: Home Assistant WebSocket failed: {exc}") from exc
        if msg.get("success"):
            return (msg.get("result") or {}).get("response")
        err = msg.get("error") or {}
        code = str(err.get("code", ""))
        text = f"{_HA_DOMAIN}.{service}: {code}: {err.get('message', '')}"
        if code in _HA_PERMANENT_ERRORS:
            raise SwitchBotRejectedError(text)
        raise SwitchBotError(text)

    async def art_frames(self) -> list[SwitchBotDevice]:
        """Discover the frame configured in Home Assistant via its local_images sensor entity."""
        state = await self._get_state()
        attrs = state.get("attributes") or {}
        friendly = str(attrs.get("friendly_name") or self._entity_id)
        if friendly.lower().endswith(" local images"):
            friendly = friendly[: -len(" local images")].strip()
        return [
            SwitchBotDevice(
                device_id=self._device_id,
                device_type=ART_FRAME,
                name=friendly or "SwitchBot Art Frame",
            )
        ]

    async def _battery(self) -> int | None:
        """The battery level of the frame's HA device (the core SwitchBot integration's battery
        sensor sits on the same device as our local_images sensor), or None if there is none or
        the token may not render templates (POST /api/template needs an admin user)."""
        template = (
            f"{{{{ device_entities(device_id('{self._entity_id}')) | select('match', 'sensor[.]') "
            "| select('is_state_attr', 'device_class', 'battery') | map('states') | first "
            "| default('') }}"
        )
        try:
            resp = await self._client.post("/api/template", json={"template": template})
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            return round(float(resp.text.strip()))
        except ValueError:
            return None  # no battery sensor, or it is unavailable/unknown

    async def art_frame_status(self, device_id: str) -> ArtFrameStatus:
        """Frame status from Home Assistant: the showing slot from the local_images sensor, the
        battery from the device's battery sensor. Display mode and firmware aren't exposed by HA,
        so they are reported as unknown, not guessed."""
        state = await self._get_state()
        attrs = state.get("attributes") or {}
        current_id = attrs.get("current_image_id")
        return ArtFrameStatus(
            device_id=device_id,
            battery=await self._battery(),
            display_mode=None,
            image_url=f"slot:{current_id}" if current_id is not None else "",
            version="",
        )

    async def upload_image_bytes(
        self,
        device_id: str,
        image: bytes,
        *,
        mime: str = "image/jpeg",
        make_room: bool = True,
    ) -> None:
        """Push ``image`` via Home Assistant's ``switchbot_art_frame.show_image`` action. With
        ``show_on_upload=False`` the frame only stores it (``show: false``, needs an integration
        version that has the option; it's left out otherwise so older versions keep working)."""
        b64 = base64.b64encode(image).decode()
        data: dict[str, Any] = {"image_data": f"data:{mime};base64,{b64}", "make_room": make_room}
        if not self._show_on_upload:
            data["show"] = False
        await self._call_service("show_image", data)

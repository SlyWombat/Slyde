"""Frame discovery over UDP, ported from ``PC_ConnectPopup`` + ``Cadre.Utils.ProcessBroadcast``.

Send ``MEMENTO_SMARTFRAME_<id>|<APP_VERSION>|<EOF>`` to ``<host>:2015`` (``host`` defaults to the
LAN broadcast address but may be a specific IP, e.g. for loopback testing). Replies arrive on UDP
2016 as ``MEMENTO_SMARTFRAME|<json>|<trailer>`` (optionally AES-encrypted as a whole).
"""

from __future__ import annotations

import contextlib
import json
import socket
import time
from dataclasses import dataclass

from . import crypto
from .protocol import APP_VERSION, DEFAULT_PORTS, MAGIC, JsonDict, Ports


@dataclass
class FrameInfo:
    """A frame's self-description from its discovery reply."""

    name: str = ""
    ip: str = ""
    mac: str = ""
    softver: float = 0.0
    hardver: float = 0.0
    size: int = 0
    orientation: str = ""
    guid: str = ""
    is_connected: bool = False
    try_and_buy: bool = False
    raw: JsonDict | None = None

    @property
    def valid(self) -> bool:
        return bool(
            self.name
            and self.softver
            and self.hardver
            and self.size
            and self.orientation
            and self.ip
        )


def parse_broadcast(response: str) -> FrameInfo | None:
    """Mirror of ``Cadre.Utils.ProcessBroadcast`` (handles plaintext or AES-encrypted replies)."""
    if not response.startswith(MAGIC):
        try:
            decrypted = crypto.aes_decrypt(response)
            if decrypted:
                response = decrypted
        except Exception:
            pass
    if not response.startswith(MAGIC):
        return None
    parts = response.split("|")
    if len(parts) != 3:
        return None
    try:
        d = json.loads(parts[1])
    except json.JSONDecodeError:
        return None

    def fnum(value: object) -> float:
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    return FrameInfo(
        name=d.get("name", ""),
        ip=d.get("ip", ""),
        mac=d.get("mac", ""),
        softver=fnum(d.get("softver", 0)),
        hardver=fnum(d.get("hardver", 0)),
        size=int(str(d.get("size", "0")) or 0),
        orientation=d.get("orientation", ""),
        guid=d.get("guid", ""),
        is_connected=bool(d.get("IsConnected", False)),
        try_and_buy=bool(d.get("TryAndBuyMode", False)),
        raw=d,
    )


def discover(
    *,
    host: str = "255.255.255.255",
    timeout: float = 6.0,
    attempts: int = 6,
    ports: Ports = DEFAULT_PORTS,
) -> list[FrameInfo]:
    """Broadcast (or unicast to ``host``) discovery requests; collect unique frame replies."""
    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        recv.bind(("", ports.broadcast_response))
    except OSError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(f"Cannot bind UDP {ports.broadcast_response}: {exc}") from exc
    recv.settimeout(0.5)

    send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    found: dict[str, FrameInfo] = {}
    deadline = time.monotonic() + timeout
    sent = 0
    next_send = 0.0
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if sent < attempts and now >= next_send:
                sent += 1
                msg = f"{MAGIC}_{sent}|{APP_VERSION}|<EOF>".encode("ascii")
                with contextlib.suppress(OSError):
                    send.sendto(msg, (host, ports.broadcast))
                next_send = now + 1.0
            try:
                data, addr = recv.recvfrom(65535)
            except TimeoutError:
                continue
            info = parse_broadcast(data.decode("ascii", errors="replace"))
            if info and info.valid:
                if not info.ip:
                    info.ip = addr[0]
                found[info.mac or info.ip] = info
    finally:
        recv.close()
        send.close()
    return list(found.values())

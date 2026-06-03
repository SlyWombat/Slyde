"""Protocol constants, command enums, and the control-channel message codec.

Wire format on the control channel (both directions):

    <.NET type FullName>|<JSON object>|<commandID>|<EOF>

Replies from the device are wrapped by Newtonsoft TypeNameHandling in a
``{"$types": {...}, "$type": "1", <real fields>}`` envelope, which we strip on decode and
reproduce on encode (the emulator) for fidelity. Command *data* sub-payloads (``sData`` /
``m_Data``) are DES-encrypted on firmware >= ENCRYPT_VERSION.

Nothing here is deployment-specific; ports are defaults overridable via :class:`Ports`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from . import crypto

# Arbitrary decoded-JSON object (the frame's payloads are heterogeneous).
JsonDict = dict[str, Any]

# --- magic / versions --------------------------------------------------------
MAGIC = "MEMENTO_SMARTFRAME"
EOF = "<EOF>"
APP_VERSION = "6"  # SetupData.APP_VERSION
ENCRYPT_VERSION = 5.0  # data payloads DES-encrypted at/above this software version
COMMUNICATION_ENDED = "COMMUNICATION_ENDED"

# --- .NET type names (assembly: CadreAndroid on the device) ------------------
T_CHANGE_SETUP = "Cadre.CommandChangeSetup"
T_CONTROL_FLOW = "Cadre.CommandControlFlow"
T_TRANSFER_FILE = "Cadre.CommandControlTransferFile"
_ASSEMBLY = "CadreAndroid, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null"

# Which object field carries the (possibly encrypted) data payload, per command type.
DATA_FIELD = {
    T_CHANGE_SETUP: "sData",
    T_CONTROL_FLOW: "m_Data",
    T_TRANSFER_FILE: "m_Data",
}


@dataclass(frozen=True)
class Ports:
    """Frame UDP/TCP ports. Defaults match firmware 6.x; override only if a frame differs."""

    broadcast: int = 2015
    broadcast_response: int = 2016
    control: int = 2017
    file: int = 2018


DEFAULT_PORTS = Ports()


class Setup(IntEnum):
    """``CommandChangeSetup.Action`` — request value, reply is the +1 ``…Done``."""

    GetConfig = 0
    SendConfig = 2
    GetCurrentAlbum = 4
    SendCurrentAlbum = 6
    SendTime = 8
    ChangeBrightness = 10
    ChangeCalibration = 12
    ChangeEvening = 14
    ChangePower = 16
    ChangeShuffle = 18
    ChangePictureDuration = 20
    ChangeThreshold = 22
    ChangeContrast = 24
    ChangeExposure = 26
    ChangeSaturation = 28
    ChangeTimeZone = 30
    ChangeOrientation = 32
    ChangeTemperature = 34
    GetFrameTime = 36


class Flow(IntEnum):
    """``CommandControlFlow.Action`` — request value, reply is the +1 ``…Done``."""

    Beacon = 0
    NextFrame = 2
    PreviousFrame = 4
    DisplayImage = 6
    DeleteImage = 8
    GetCurrentImageName = 10
    SendCurrentImageName = 12
    ForgetNetwork = 14
    FactoryReset = 16
    TriggerUpdate = 18
    Disconnect = 20


class Transfer(IntEnum):
    """``CommandControlTransferFile.Action`` — groups of 5: base/Started/Ended/Succeeded/Failed."""

    ReadFile = 0
    ReadFileStarted = 1
    ReadFileEnded = 2
    ReadFileSucceeded = 3
    ReadFileFailed = 4
    WriteFile = 5
    WriteFileStarted = 6
    WriteFileEnded = 7
    WriteFileSucceeded = 8
    WriteFileFailed = 9
    GetThumbnailsList = 10
    GetThumbnailsListStarted = 11
    GetThumbnailsListEnded = 12
    GetThumbnailsListSucceeded = 13
    GetThumbnailsListFailed = 14
    GetThumbnails = 15
    GetThumbnailsStarted = 16
    GetThumbnailsEnded = 17
    GetThumbnailsSucceeded = 18
    GetThumbnailsFailed = 19
    GetAlbums = 20
    GetAlbumsStarted = 21
    GetAlbumsEnded = 22
    GetAlbumsSucceeded = 23
    GetAlbumsFailed = 24
    SendAlbums = 25
    SendAlbumsStarted = 26
    SendAlbumsEnded = 27
    SendAlbumsSucceeded = 28
    SendAlbumsFailed = 29


@dataclass
class Message:
    """A decoded control-channel message."""

    type: str
    obj: JsonDict
    cid: int = -1

    @property
    def action(self) -> int:
        value = self.obj.get("m_Action")
        return int(value) if value is not None else -1

    def data(self) -> str:
        """Decrypted data sub-payload (from whichever field this command type uses)."""
        raw = self.obj.get(DATA_FIELD.get(self.type, "sData")) or self.obj.get("m_Data") or ""
        return crypto.maybe_des_decrypt(raw) if raw else ""

    def json(self) -> object:
        text = self.data().strip()
        return json.loads(text) if text.startswith(("{", "[")) else text


def encode(
    type_name: str, action: int, *, data: str = "", extra: JsonDict | None = None, cid: int
) -> bytes:
    """Build an outbound control message. ``data`` (if given) is DES-encrypted into the
    type's data field. ``extra`` adds/overrides top-level object fields."""
    obj: dict[str, object] = {"m_Action": int(action), "m_Socket": None}
    if data:
        obj[DATA_FIELD.get(type_name, "sData")] = crypto.des_encrypt(data)
    if extra:
        obj.update(extra)
    return f"{type_name}|{json.dumps(obj)}|{cid}|{EOF}".encode()


def encode_reply(type_name: str, action: int, *, data: str = "", cid: int = 0) -> bytes:
    """Build a device-style reply, including the Newtonsoft ``$type`` envelope."""
    full = f"{type_name}, {_ASSEMBLY}"
    obj: dict[str, object] = {"$types": {full: "1"}, "$type": "1", "m_Action": int(action)}
    if data:
        obj[DATA_FIELD.get(type_name, "sData")] = crypto.des_encrypt(data)
    return f"{type_name}|{json.dumps(obj)}|{cid}|{EOF}".encode()


@dataclass
class Decoder:
    """Incremental decoder; feed bytes, yield complete :class:`Message` objects."""

    _buf: bytes = field(default=b"")

    def feed(self, chunk: bytes) -> list[Message]:
        self._buf += chunk
        out: list[Message] = []
        marker = EOF.encode()
        while marker in self._buf:
            raw, self._buf = self._buf.split(marker, 1)
            text = raw.decode("utf-8", "replace")
            if not text.strip() or text.startswith(COMMUNICATION_ENDED):
                continue
            parts = text.split("|")
            try:
                obj = json.loads(parts[1]) if len(parts) > 1 else {}
            except json.JSONDecodeError:
                obj = {}
            cid = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else -1
            out.append(Message(type=parts[0], obj=obj, cid=cid))
        return out

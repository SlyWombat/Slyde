"""High-level frame client: owns the control + file channels and exposes frame operations."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .control import ControlChannel
from .protocol import (
    DEFAULT_PORTS,
    T_CHANGE_SETUP,
    T_CONTROL_FLOW,
    T_TRANSFER_FILE,
    Flow,
    JsonDict,
    Ports,
    Setup,
    Transfer,
)
from .transfer import FileChannel


class FrameError(RuntimeError):
    """Raised when the frame reports a failure for a requested operation."""


class FrameClient:
    """A connected session to one Memento frame.

    Like the official app, this opens both the control (2017) and file (2018) channels.
    """

    def __init__(self, host: str, *, ports: Ports = DEFAULT_PORTS, timeout: float = 10.0) -> None:
        self.host = host
        self.ports = ports
        self.control = ControlChannel(host, ports, timeout)
        self.file = FileChannel(host, ports)

    # -- lifecycle ------------------------------------------------------------
    def connect(self) -> FrameClient:
        self.control.connect()
        self.file.connect()
        return self

    def close(self) -> None:
        self.file.close()
        self.control.close()

    def __enter__(self) -> FrameClient:
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- setup / config reads -------------------------------------------------
    def get_config(self) -> JsonDict:
        reply = self.control.request(T_CHANGE_SETUP, Setup.GetConfig)
        return _as_dict(reply.json())

    def get_frame_time(self) -> JsonDict:
        reply = self.control.request(T_CHANGE_SETUP, Setup.GetFrameTime)
        return _as_dict(reply.json())

    def get_current_album(self) -> object:
        return self.control.request(T_CHANGE_SETUP, Setup.GetCurrentAlbum).json()

    # -- setup writes ---------------------------------------------------------
    def change_setup(self, action: Setup, payload: JsonDict) -> None:
        """Generic setup mutation. ``payload`` is JSON-serialized and DES-encrypted into sData."""
        self.control.request(T_CHANGE_SETUP, action, data=json.dumps(payload))

    # -- display controls -----------------------------------------------------
    def next_image(self) -> None:
        self.control.request(T_CONTROL_FLOW, Flow.NextFrame)

    def previous_image(self) -> None:
        self.control.request(T_CONTROL_FLOW, Flow.PreviousFrame)

    def get_current_image_name(self) -> str:
        reply = self.control.request(T_CONTROL_FLOW, Flow.GetCurrentImageName)
        payload = reply.json()
        if isinstance(payload, dict) and payload.get("srcfilename"):
            return str(payload["srcfilename"])
        return str(reply.obj.get("m_SourceFileName", ""))

    def delete_image(self, filename: str) -> None:
        self.control.request(
            T_CONTROL_FLOW, Flow.DeleteImage, data=json.dumps({"filenames": [filename]})
        )

    # -- file transfer --------------------------------------------------------
    def upload_image(
        self,
        data: bytes,
        dest_name: str,
        *,
        info: JsonDict | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Upload ``data`` as ``dest_name`` (WriteFile handshake + raw bytes on file channel)."""
        payload = json.dumps(
            {
                "srcfilename": dest_name,
                "dstfilename": dest_name,
                "filesize": str(len(data)),
                "info": info or {},
            }
        )
        self.control.send(T_TRANSFER_FILE, Transfer.WriteFile, data=payload)
        self.control.wait_for(
            T_TRANSFER_FILE, [Transfer.WriteFileStarted, Transfer.WriteFileFailed]
        )
        self.file.send_bytes(data, progress=progress)
        self.control.send(T_TRANSFER_FILE, Transfer.WriteFileEnded, data=payload)
        result = self.control.wait_for(
            T_TRANSFER_FILE, [Transfer.WriteFileSucceeded, Transfer.WriteFileFailed]
        )
        if result.action == Transfer.WriteFileFailed:
            raise FrameError(f"frame rejected upload of {dest_name!r}")

    def upload_file(self, path: str | Path, dest_name: str | None = None, **kwargs: object) -> None:
        p = Path(path)
        self.upload_image(p.read_bytes(), dest_name or p.name, **kwargs)  # type: ignore[arg-type]

    def get_albums(self) -> bytes:
        """Request the albums data file; returns the raw bytes the frame sends."""
        self.control.send(
            T_TRANSFER_FILE, Transfer.GetAlbums, data=json.dumps({"dstfilename": "albums.dat"})
        )
        started = self.control.wait_for(
            T_TRANSFER_FILE, [Transfer.GetAlbumsStarted, Transfer.GetAlbumsFailed]
        )
        if started.action == Transfer.GetAlbumsFailed:
            raise FrameError("frame failed to start albums transfer")
        size = int(_as_dict(started.json()).get("filesize", 0))
        data = self.file.recv_bytes(size) if size else b""
        self.control.send(
            T_TRANSFER_FILE, Transfer.GetAlbumsEnded, data=json.dumps({"dstfilename": "albums.dat"})
        )
        self.control.wait_for(
            T_TRANSFER_FILE, [Transfer.GetAlbumsSucceeded, Transfer.GetAlbumsFailed]
        )
        return data


def _as_dict(value: object) -> JsonDict:
    return value if isinstance(value, dict) else {}

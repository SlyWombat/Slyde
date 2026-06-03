"""High-level frame client: owns the control + file channels and exposes frame operations."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from . import crypto
from .albums import AlbumData, parse_album_data
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

ALBUM_DATA_FILE = "AlbumData.json"
THUMBNAILS_LIST_FILE = "ThumbnailsList.txt"


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

    # -- file transfer (generic) ----------------------------------------------
    # Transfer actions come in groups of 5: base, +1 Started, +2 Ended, +3 Succeeded, +4 Failed.
    def _download(self, base: Transfer, dest: str) -> bytes:
        started, ended, ok, failed = base + 1, base + 2, base + 3, base + 4
        self.control.send(T_TRANSFER_FILE, base, data=json.dumps({"dstfilename": dest}))
        s = self.control.wait_for(T_TRANSFER_FILE, [started, failed])
        if s.action == failed:
            raise FrameError(f"frame failed to start transfer {base.name}")
        data = self.file.recv_bytes(s.file_size) if s.file_size else b""
        self.control.send(T_TRANSFER_FILE, ended, data=json.dumps({"dstfilename": dest}))
        self.control.wait_for(T_TRANSFER_FILE, [ok, failed])
        return data

    def _upload(
        self,
        base: Transfer,
        data: bytes,
        dest: str,
        *,
        progress: Callable[[int, int], None] | None = None,
        info: JsonDict | None = None,
    ) -> None:
        started, ended, ok, failed = base + 1, base + 2, base + 3, base + 4
        payload = json.dumps(
            {
                "srcfilename": dest,
                "dstfilename": dest,
                "filesize": str(len(data)),
                "info": info or {},
            }
        )
        self.control.send(T_TRANSFER_FILE, base, data=payload)
        self.control.wait_for(T_TRANSFER_FILE, [started, failed])
        self.file.send_bytes(data, progress=progress)
        self.control.send(T_TRANSFER_FILE, ended, data=payload)
        result = self.control.wait_for(T_TRANSFER_FILE, [ok, failed])
        if result.action == failed:
            raise FrameError(f"frame rejected transfer {base.name} of {dest!r}")

    # -- images ---------------------------------------------------------------
    def upload_image(
        self,
        data: bytes,
        dest_name: str,
        *,
        info: JsonDict | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Upload ``data`` as ``dest_name`` (WriteFile handshake + raw bytes on file channel)."""
        self.upload(data, dest_name, info=info, progress=progress)

    def upload(
        self,
        data: bytes,
        dest_name: str,
        *,
        info: JsonDict | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self._upload(Transfer.WriteFile, data, dest_name, info=info, progress=progress)

    def upload_file(self, path: str | Path, dest_name: str | None = None, **kwargs: object) -> None:
        p = Path(path)
        self.upload_image(p.read_bytes(), dest_name or p.name, **kwargs)  # type: ignore[arg-type]

    # -- albums ---------------------------------------------------------------
    def get_album_data(self) -> AlbumData:
        """Download + AES-decrypt + parse the frame's album structure."""
        raw = self._download(Transfer.GetAlbums, ALBUM_DATA_FILE).decode("utf-8", "replace")
        return parse_album_data(crypto.maybe_aes_decrypt(raw))

    def send_album_data(self, album_data: AlbumData) -> None:
        """AES-encrypt + upload the album structure back to the frame."""
        encrypted = crypto.aes_encrypt(album_data.to_json()).encode("utf-8")
        self._upload(Transfer.SendAlbums, encrypted, ALBUM_DATA_FILE)

    # -- thumbnails -----------------------------------------------------------
    def get_thumbnails_list(self) -> list[tuple[str, str]]:
        """Return (image_filename, md5) for every image on the frame (from ThumbnailsList.txt)."""
        text = self._download(Transfer.GetThumbnailsList, THUMBNAILS_LIST_FILE).decode(
            "utf-8", "replace"
        )
        out: list[tuple[str, str]] = []
        for line in text.splitlines():
            if "|" not in line:
                continue  # header line ("Memento Version x.y")
            name, _, md5 = line.partition("|")
            out.append((thumb_to_image(name.strip()), md5.strip()))
        return out

    def get_thumbnail(self, image_filename: str) -> bytes:
        """Fetch the ``<name>.thumb.png`` thumbnail bytes for an image."""
        return self._download(Transfer.GetThumbnails, image_to_thumb(image_filename))


def image_to_thumb(image_filename: str) -> str:
    stem = image_filename.rsplit(".", 1)[0]
    return f"{stem}.thumb.png"


def thumb_to_image(thumb_filename: str) -> str:
    return (
        thumb_filename[: -len(".thumb.png")] + ".jpg"
        if thumb_filename.endswith(".thumb.png")
        else thumb_filename
    )


def _as_dict(value: object) -> JsonDict:
    return value if isinstance(value, dict) else {}

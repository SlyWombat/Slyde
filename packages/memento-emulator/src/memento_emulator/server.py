"""A faithful, threaded emulator of the Memento frame's server side.

Implements UDP discovery (2015 request / 2016 reply) and the TCP control (2017) and file (2018)
channels, including DES-encrypted command payloads and the Newtonsoft ``$type`` reply envelope.

The control handler drives the matching file socket inline (matched by client IP), so uploads and
downloads need no cross-thread signalling. Intended for a single client at a time (tests/dev).
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic

from memento_core.albums import parse_album_data
from memento_core.crypto import aes_encrypt, maybe_aes_decrypt
from memento_core.protocol import (
    DEFAULT_PORTS,
    MAGIC,
    T_CHANGE_SETUP,
    T_CONTROL_FLOW,
    T_TRANSFER_FILE,
    Decoder,
    Flow,
    JsonDict,
    Message,
    Ports,
    Setup,
    Transfer,
    encode_reply,
)

from .state import FrameState

# A TCP connection handler: (connection, peer address) -> None.
ConnHandler = Callable[[socket.socket, tuple[str, int]], None]


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    got = 0
    while got < n:
        chunk = sock.recv(min(262144, n - got))
        if not chunk:
            raise ConnectionError("file socket closed mid-transfer")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


class EmulatedFrame:
    """A run-once emulated frame. Use as a context manager or call start()/stop()."""

    def __init__(
        self,
        state: FrameState | None = None,
        *,
        host: str = "127.0.0.1",
        ports: Ports = DEFAULT_PORTS,
        on_update: Callable[[str, str], object] | None = None,
    ) -> None:
        self.state = state or FrameState(ip=host)
        self.host = host
        self.ports = ports
        # Called with (url, md5) when the frame is told to self-update (display/device mode wires a
        # real updater; the emulator/tests leave it None and just record the request).
        self._on_update = on_update
        self.last_update: tuple[str, str] | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._sockets: list[socket.socket] = []
        # One queue of unclaimed file connections per client IP; each control session claims one,
        # pairing control<->file connections (the client opens control then file). This avoids
        # reusing a stale file socket from an earlier, already-closed session.
        self._file_socks: dict[str, list[socket.socket]] = {}
        self._file_cv = threading.Condition()

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> EmulatedFrame:
        self._serve_udp()
        self._serve_tcp(self.ports.control, self._handle_control)
        self._serve_tcp(self.ports.file, self._register_file_socket)
        self._spawn(self._cycle_loop)
        return self

    def stop(self) -> None:
        self._stop.set()
        for s in self._sockets:
            with contextlib.suppress(OSError):
                s.close()
        for t in self._threads:
            t.join(timeout=2.0)

    def __enter__(self) -> EmulatedFrame:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _spawn(self, fn: Callable[..., object], *args: object) -> None:
        t = threading.Thread(target=fn, args=args, daemon=True)
        t.start()
        self._threads.append(t)

    # -- slideshow ------------------------------------------------------------
    def _cycle_loop(self) -> None:
        """Auto-advance the displayed image every ``DisplayTime`` seconds, like the real frame.

        Honours ``DisplayOn`` (paused when off) and ``ShuffleOn`` (random vs sequential). Config
        is re-read every tick so changes from the manager take effect without a restart.
        """
        last = monotonic()
        while not self._stop.wait(0.5):
            config = self.state.config
            if not config.get("DisplayOn", True):
                last = monotonic()
                continue
            interval = max(1, int(config.get("DisplayTime", 60) or 60))
            if monotonic() - last >= interval:
                last = monotonic()
                self.state.advance(shuffle=bool(config.get("ShuffleOn", False)))

    # -- UDP discovery --------------------------------------------------------
    def _serve_udp(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.ports.broadcast))
        sock.settimeout(0.3)
        self._sockets.append(sock)
        self._spawn(self._udp_loop, sock)

    def _udp_loop(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            if not data.decode("ascii", "replace").startswith(MAGIC):
                continue
            reply = f"{MAGIC}|{json.dumps(self.state.discovery_info())}|{'<EOF>'}".encode()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as out:
                out.sendto(reply, (addr[0], self.ports.broadcast_response))

    # -- TCP accept -----------------------------------------------------------
    def _serve_tcp(self, port: int, handler: ConnHandler) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, port))
        sock.listen(8)
        sock.settimeout(0.3)
        self._sockets.append(sock)
        self._spawn(self._accept_loop, sock, handler)

    def _accept_loop(self, sock: socket.socket, handler: ConnHandler) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self._sockets.append(conn)
            self._spawn(handler, conn, addr)

    # -- file channel ---------------------------------------------------------
    def _register_file_socket(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        with self._file_cv:
            self._file_socks.setdefault(addr[0], []).append(conn)
            self._file_cv.notify_all()
        # Hold the connection open; the paired control session reads/writes it for transfers.
        while not self._stop.is_set():
            if self._stop.wait(0.5):
                break

    def _claim_file_socket(self, ip: str, timeout: float = 5.0) -> socket.socket:
        """Take ownership of the next unclaimed file connection from ``ip``."""
        with self._file_cv:
            if not self._file_cv.wait_for(lambda: bool(self._file_socks.get(ip)), timeout=timeout):
                raise TimeoutError(f"no file socket from {ip}")
            return self._file_socks[ip].pop(0)

    # -- control channel ------------------------------------------------------
    def _handle_control(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        decoder = Decoder()
        pending: list[Message] = []
        ip = addr[0]
        claimed: list[socket.socket | None] = [None]

        def file_sock() -> socket.socket:
            sock = claimed[0]
            if sock is None:
                sock = self._claim_file_socket(ip)
                claimed[0] = sock
            return sock

        try:
            while not self._stop.is_set():
                while not pending:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    pending.extend(decoder.feed(chunk))
                self._dispatch(conn, file_sock, pending.pop(0))
        except (ConnectionError, OSError):
            return

    def _dispatch(
        self, conn: socket.socket, file_sock: Callable[[], socket.socket], msg: Message
    ) -> None:
        if msg.type == T_CHANGE_SETUP:
            self._dispatch_setup(conn, msg)
        elif msg.type == T_CONTROL_FLOW:
            self._dispatch_flow(conn, msg)
        elif msg.type == T_TRANSFER_FILE:
            self._dispatch_transfer(conn, file_sock, msg)

    def _reply(
        self,
        conn: socket.socket,
        type_name: str,
        action: int,
        *,
        data: str = "",
        file_size: int | None = None,
        cid: int = 0,
    ) -> None:
        conn.sendall(encode_reply(type_name, action, data=data, file_size=file_size, cid=cid))

    def _dispatch_setup(self, conn: socket.socket, msg: Message) -> None:
        action = msg.action
        if action == Setup.GetConfig:
            self._reply(
                conn, T_CHANGE_SETUP, Setup.GetConfig + 1, data=json.dumps(self.state.config)
            )
        elif action == Setup.GetFrameTime:
            now = datetime.now(UTC).strftime("%m/%d/%Y %H:%M:%S")
            self._reply(
                conn,
                T_CHANGE_SETUP,
                Setup.GetFrameTime + 1,
                data=json.dumps({"DateTime": now, "ServerTime": "False"}),
            )
        elif action == Setup.GetCurrentAlbum:
            album = {"Name": self.state.current_album, "Images": self.state.current_album_images()}
            self._reply(conn, T_CHANGE_SETUP, Setup.GetCurrentAlbum + 1, data=json.dumps(album))
        elif action == Setup.SendCurrentAlbum:
            payload = msg.json()
            if isinstance(payload, dict) and payload.get("Name"):
                self.state.set_current_album(str(payload["Name"]))
            self._reply(conn, T_CHANGE_SETUP, action + 1)
        elif action == Setup.SendTime:
            # The frame sets its RTC; the emulator answers GetFrameTime from host time, so just ack
            # (don't merge the time payload into the config).
            self._reply(conn, T_CHANGE_SETUP, action + 1)
        elif action == Setup.ChangeOrientation:
            payload = msg.json()
            if isinstance(payload, dict):
                self.state.set_orientation(payload)
            self._reply(conn, T_CHANGE_SETUP, action + 1)
        else:
            # SendConfig / other Change* — apply the patch if it's a config-ish object, then ack.
            payload = msg.json()
            if isinstance(payload, dict):
                self.state.update_config(payload)
            self._reply(conn, T_CHANGE_SETUP, action + 1)

    def _dispatch_flow(self, conn: socket.socket, msg: Message) -> None:
        action = msg.action
        if action == Flow.DeleteImage:
            payload = msg.json()
            names = payload.get("filenames", []) if isinstance(payload, dict) else []
            for name in names:
                self.state.remove_photo(name)
            self._reply(conn, T_CONTROL_FLOW, action + 1)
        elif action == Flow.GetCurrentImageName:
            self._reply(
                conn,
                T_CONTROL_FLOW,
                action + 1,
                data=json.dumps({"srcfilename": self.state.current_image}),
            )
        elif action == Flow.NextFrame:
            self.state.advance(shuffle=bool(self.state.config.get("ShuffleOn", False)))
            self._reply(conn, T_CONTROL_FLOW, action + 1)
        elif action == Flow.PreviousFrame:
            self.state.advance(step=-1)
            self._reply(conn, T_CONTROL_FLOW, action + 1)
        elif action == Flow.DisplayImage:
            payload = msg.json()
            name = (
                payload.get("srcfilename") or payload.get("m_SourceFileName")
                if (isinstance(payload, dict))
                else None
            )
            if name:
                self.state.show_image(str(name))
            self._reply(conn, T_CONTROL_FLOW, action + 1)
        elif action == Flow.FactoryReset:
            self.state.factory_reset()
            self._reply(conn, T_CONTROL_FLOW, action + 1)
        elif action == Flow.TriggerUpdate:
            payload = msg.json()
            if isinstance(payload, dict) and payload.get("url"):
                self.last_update = (str(payload["url"]), str(payload.get("md5", "")))
                if self._on_update is not None:
                    # Apply off the control thread (a real updater downloads + restarts).
                    self._spawn(self._on_update, *self.last_update)
            self._reply(conn, T_CONTROL_FLOW, action + 1)
        else:
            self._reply(conn, T_CONTROL_FLOW, action + 1)

    def _serve_download(
        self, conn: socket.socket, file_sock: Callable[[], socket.socket], started: int, blob: bytes
    ) -> None:
        self._reply(conn, T_TRANSFER_FILE, started, file_size=len(blob))
        file_sock().sendall(blob)

    def _serve_upload(
        self,
        conn: socket.socket,
        file_sock: Callable[[], socket.socket],
        info: JsonDict,
        started: int,
    ) -> tuple[str, bytes]:
        dest = (info.get("dstfilename") or "upload").lower()
        size = int(info.get("filesize", 0) or 0)
        self._reply(conn, T_TRANSFER_FILE, started, data=json.dumps(info))
        data = _recv_exact(file_sock(), size) if size else b""
        return dest, data

    def _dispatch_transfer(
        self, conn: socket.socket, file_sock: Callable[[], socket.socket], msg: Message
    ) -> None:
        action = msg.action
        payload = msg.json()
        info = payload if isinstance(payload, dict) else {}
        # Uploads (client -> frame)
        if action == Transfer.WriteFile:
            dest, data = self._serve_upload(conn, file_sock, info, Transfer.WriteFileStarted)
            self.state.add_photo(dest, data)
        elif action == Transfer.WriteFileEnded:
            self._reply(conn, T_TRANSFER_FILE, Transfer.WriteFileSucceeded)
        elif action == Transfer.SendAlbums:
            _, data = self._serve_upload(conn, file_sock, info, Transfer.SendAlbumsStarted)
            text = maybe_aes_decrypt(data.decode("utf-8", "replace"))
            self.state.set_albums(parse_album_data(text))
        elif action == Transfer.SendAlbumsEnded:
            self._reply(conn, T_TRANSFER_FILE, Transfer.SendAlbumsSucceeded)
        # Downloads (frame -> client)
        elif action == Transfer.ReadFile:
            name = str(info.get("srcfilename") or info.get("dstfilename") or "")
            blob = self.state.get_photo(name) or b""
            self._serve_download(conn, file_sock, Transfer.ReadFileStarted, blob)
        elif action == Transfer.ReadFileEnded:
            self._reply(conn, T_TRANSFER_FILE, Transfer.ReadFileSucceeded)
        elif action == Transfer.GetAlbums:
            blob = aes_encrypt(self.state.albums.to_json()).encode("utf-8")
            self._serve_download(conn, file_sock, Transfer.GetAlbumsStarted, blob)
        elif action == Transfer.GetAlbumsEnded:
            self._reply(conn, T_TRANSFER_FILE, Transfer.GetAlbumsSucceeded)
        elif action == Transfer.GetThumbnailsList:
            blob = self.state.thumbnails_list_text().encode("utf-8")
            self._serve_download(conn, file_sock, Transfer.GetThumbnailsListStarted, blob)
        elif action == Transfer.GetThumbnailsListEnded:
            self._reply(conn, T_TRANSFER_FILE, Transfer.GetThumbnailsListSucceeded)
        elif action == Transfer.GetThumbnails:
            blob = self.state.thumbnail_for(str(info.get("dstfilename", "")))
            self._serve_download(conn, file_sock, Transfer.GetThumbnailsStarted, blob)
        elif action == Transfer.GetThumbnailsEnded:
            self._reply(conn, T_TRANSFER_FILE, Transfer.GetThumbnailsSucceeded)

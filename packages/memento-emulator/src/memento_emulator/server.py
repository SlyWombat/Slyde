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

from memento_core.protocol import (
    DEFAULT_PORTS,
    MAGIC,
    T_CHANGE_SETUP,
    T_CONTROL_FLOW,
    T_TRANSFER_FILE,
    Decoder,
    Flow,
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
    ) -> None:
        self.state = state or FrameState(ip=host)
        self.host = host
        self.ports = ports
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._sockets: list[socket.socket] = []
        self._file_socks: dict[str, socket.socket] = {}
        self._file_cv = threading.Condition()

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> EmulatedFrame:
        self._serve_udp()
        self._serve_tcp(self.ports.control, self._handle_control)
        self._serve_tcp(self.ports.file, self._register_file_socket)
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
            self._file_socks[addr[0]] = conn
            self._file_cv.notify_all()
        # Hold the connection open; the control handler reads/writes it for transfers.
        while not self._stop.is_set():
            if self._stop.wait(0.5):
                break

    def _file_socket_for(self, ip: str, timeout: float = 5.0) -> socket.socket:
        with self._file_cv:
            if not self._file_cv.wait_for(lambda: ip in self._file_socks, timeout=timeout):
                raise TimeoutError(f"no file socket from {ip}")
            return self._file_socks[ip]

    # -- control channel ------------------------------------------------------
    def _handle_control(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        decoder = Decoder()
        pending: list[Message] = []
        ip = addr[0]
        try:
            while not self._stop.is_set():
                while not pending:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    pending.extend(decoder.feed(chunk))
                self._dispatch(conn, ip, pending.pop(0))
        except (ConnectionError, OSError):
            return

    def _dispatch(self, conn: socket.socket, ip: str, msg: Message) -> None:
        if msg.type == T_CHANGE_SETUP:
            self._dispatch_setup(conn, msg)
        elif msg.type == T_CONTROL_FLOW:
            self._dispatch_flow(conn, msg)
        elif msg.type == T_TRANSFER_FILE:
            self._dispatch_transfer(conn, ip, msg)

    def _reply(
        self, conn: socket.socket, type_name: str, action: int, *, data: str = "", cid: int = 0
    ) -> None:
        conn.sendall(encode_reply(type_name, action, data=data, cid=cid))

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
            album = {"Name": self.state.current_album, "Images": self.state.photo_names()}
            self._reply(conn, T_CHANGE_SETUP, Setup.GetCurrentAlbum + 1, data=json.dumps(album))
        else:
            # SendConfig / Change* — apply patch if the payload is a config-ish object, then ack.
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
        else:
            self._reply(conn, T_CONTROL_FLOW, action + 1)

    def _dispatch_transfer(self, conn: socket.socket, ip: str, msg: Message) -> None:
        action = msg.action
        payload = msg.json()
        info = payload if isinstance(payload, dict) else {}
        if action == Transfer.WriteFile:
            dest = (info.get("dstfilename") or "upload.jpg").lower()
            size = int(info.get("filesize", 0) or 0)
            self._reply(conn, T_TRANSFER_FILE, Transfer.WriteFileStarted, data=json.dumps(info))
            fs = self._file_socket_for(ip)
            data = _recv_exact(fs, size) if size else b""
            self.state.add_photo(dest, data)
        elif action == Transfer.WriteFileEnded:
            self._reply(conn, T_TRANSFER_FILE, Transfer.WriteFileSucceeded)
        elif action == Transfer.GetAlbums:
            blob = json.dumps(
                {"album": self.state.current_album, "images": self.state.photo_names()}
            ).encode()
            started = {"dstfilename": "albums.dat", "filesize": str(len(blob))}
            self._reply(conn, T_TRANSFER_FILE, Transfer.GetAlbumsStarted, data=json.dumps(started))
            fs = self._file_socket_for(ip)
            fs.sendall(blob)
        elif action == Transfer.GetAlbumsEnded:
            self._reply(conn, T_TRANSFER_FILE, Transfer.GetAlbumsSucceeded)

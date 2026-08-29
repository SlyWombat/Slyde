"""File channel client (TCP 2018): a raw, unframed byte stream.

The byte count for any transfer is announced beforehand on the control channel
(the ``filesize`` field), so this channel just moves exactly that many bytes.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

from .protocol import Ports

CHUNK = 262144  # 256 KiB, matches the official client's send buffer


class FileChannel:
    """Synchronous file-transfer connection to a frame (or emulator)."""

    def __init__(self, host: str, ports: Ports, timeout: float = 60.0) -> None:
        self._host = host
        self._ports = ports
        self._timeout = timeout
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        """Open the file connection (idempotent — opened lazily, only when a transfer needs it)."""
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect((self._host, self._ports.file))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            sock.close()  # same: don't leak the socket when the frame won't take the connection
            raise
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    def socket(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("file channel not connected")
        return self._sock

    def send_bytes(self, data: bytes, progress: Callable[[int, int], None] | None = None) -> None:
        total = len(data)
        sent = 0
        while sent < total:
            n = self.socket.send(data[sent : sent + CHUNK])
            if n == 0:
                raise ConnectionError("file channel closed during send")
            sent += n
            if progress:
                progress(sent, total)

    def recv_until_idle(self, limit: int, *, sentinel: bytes | None = None) -> bytes:
        """Read up to ``limit`` bytes, stopping when the frame goes quiet or ``sentinel`` lands.

        The Memento frame's ``ReadFile`` never stats the file: it streams from whatever size the
        CLIENT announced (#72). Since a client has no way to learn a photo's true length -- the
        thumbnails list carries md5s, not sizes -- the only way to fetch one is to over-announce and
        read until the bytes stop. ``sentinel`` (a JPEG end-of-image marker) ends the read the
        instant the file is complete, so the common case costs no idle wait at all.
        """
        chunks: list[bytes] = []
        received = 0
        tail = b""
        while received < limit:
            try:
                chunk = self.socket.recv(min(CHUNK, limit - received))
            except TimeoutError:
                break  # the frame has sent everything it had
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if sentinel:
                # Check across the chunk boundary, so a marker split in two is still seen.
                tail = (tail + chunk)[-(len(sentinel) + 8) :]
                if tail.rstrip().endswith(sentinel):
                    break
        return b"".join(chunks)

    def recv_bytes(self, size: int, progress: Callable[[int, int], None] | None = None) -> bytes:
        chunks: list[bytes] = []
        received = 0
        while received < size:
            chunk = self.socket.recv(min(CHUNK, size - received))
            if not chunk:
                raise ConnectionError("file channel closed during receive")
            chunks.append(chunk)
            received += len(chunk)
            if progress:
                progress(received, size)
        return b"".join(chunks)

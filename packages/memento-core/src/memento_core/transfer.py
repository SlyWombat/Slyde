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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        sock.connect((self._host, self._ports.file))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
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

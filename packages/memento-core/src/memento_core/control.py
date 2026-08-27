"""Control channel client (TCP 2017): JSON commands framed as ``type|json|cid|<EOF>``."""

from __future__ import annotations

import select
import socket
from collections.abc import Iterable

from .protocol import Decoder, JsonDict, Message, Ports, encode


class ControlChannel:
    """Synchronous control-channel connection to a frame (or emulator)."""

    def __init__(self, host: str, ports: Ports, timeout: float = 10.0) -> None:
        self._host = host
        self._ports = ports
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._decoder = Decoder()
        self._pending: list[Message] = []
        self._cid = 0

    def connect(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect((self._host, self._ports.control))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            sock.close()  # a refused/timed-out connect must not leak the socket — callers retry
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
            raise RuntimeError("control channel not connected")
        return self._sock

    def send(
        self, type_name: str, action: int, *, data: str = "", extra: JsonDict | None = None
    ) -> int:
        self._cid += 1
        self.socket.sendall(encode(type_name, action, data=data, extra=extra, cid=self._cid))
        return self._cid

    def closed_by_peer(self, timeout: float = 0.0) -> bool:
        """Has the frame torn this control session down? Waits up to ``timeout`` for an answer.

        Non-destructive: a real message that arrives while we watch is buffered for the next
        ``recv`` rather than dropped. A real Memento frame closes the control session about every
        21s (see ``docs/protocol.md`` -- the service tick, #71), so a caller holding a warm session
        polls this to notice at once and reconnect while still in phase with the tick.
        """
        sock = self._sock
        if sock is None:
            return True
        try:
            ready, _, _ = select.select([sock], [], [], max(0.0, timeout))
        except OSError:
            return True
        if not ready:
            return False
        try:
            chunk = sock.recv(65536)
        except (TimeoutError, BlockingIOError):
            return False
        except OSError:
            return True
        if not chunk:
            return True
        self._pending.extend(self._decoder.feed(chunk))
        return False

    def recv(self) -> Message:
        """Return the next decoded message, reading from the socket as needed."""
        while not self._pending:
            chunk = self.socket.recv(65536)
            if not chunk:
                raise ConnectionError("control channel closed by peer")
            self._pending.extend(self._decoder.feed(chunk))
        return self._pending.pop(0)

    def request(
        self, type_name: str, action: int, *, data: str = "", extra: JsonDict | None = None
    ) -> Message:
        """Send a command and return the first reply of the same command type."""
        self.send(type_name, action, data=data, extra=extra)
        while True:
            msg = self.recv()
            if msg.type == type_name:
                return msg

    def wait_for(self, type_name: str, actions: Iterable[int]) -> Message:
        """Read until a message of ``type_name`` whose action is in ``actions``."""
        wanted = set(actions)
        while True:
            msg = self.recv()
            if msg.type == type_name and msg.action in wanted:
                return msg

"""Control-message codec: framing, encryption of data fields, and the reply envelope."""

from __future__ import annotations

import json
import time

import pytest

from memento_core import crypto
from memento_core.protocol import (
    EOF,
    T_CHANGE_SETUP,
    T_TRANSFER_FILE,
    Decoder,
    Setup,
    encode,
    encode_reply,
)


def test_encode_frames_type_json_cid_eof() -> None:
    wire = encode(T_CHANGE_SETUP, Setup.GetConfig, cid=7).decode()
    assert wire.endswith(f"|7|{EOF}")
    type_name, body, cid, _ = wire.split("|")
    assert type_name == T_CHANGE_SETUP
    assert json.loads(body)["m_Action"] == int(Setup.GetConfig)
    assert cid == "7"


def test_encode_des_encrypts_data_field() -> None:
    wire = encode(T_CHANGE_SETUP, Setup.SendConfig, data='{"Name":"X"}', cid=1).decode()
    obj = json.loads(wire.split("|")[1])
    assert obj["sData"] != '{"Name":"X"}'  # encrypted
    assert crypto.des_decrypt(obj["sData"]) == '{"Name":"X"}'


def test_decoder_handles_split_and_multiple_messages() -> None:
    a = encode(T_CHANGE_SETUP, Setup.GetConfig, cid=1)
    b = encode(T_CHANGE_SETUP, Setup.GetFrameTime, cid=2)
    dec = Decoder()
    assert dec.feed(a[:5]) == []  # partial, nothing yet
    msgs = dec.feed(a[5:] + b)
    assert [m.cid for m in msgs] == [1, 2]
    assert msgs[0].action == int(Setup.GetConfig)


def test_reply_envelope_decodes_and_decrypts() -> None:
    payload = {"DateTime": "01/01/0001 00:00:19", "ServerTime": "False"}
    wire = encode_reply(T_CHANGE_SETUP, Setup.GetFrameTime + 1, data=json.dumps(payload))
    [msg] = Decoder().feed(wire)
    assert msg.obj.get("$type") == "1"  # Newtonsoft envelope present
    assert msg.json() == payload  # ...and the real data decrypts cleanly


class _ScriptedSocket:
    """A control socket that replays canned frames, then stalls — for testing the wait bounds."""

    def __init__(
        self,
        chunks: list[bytes],
        *,
        chatter: bytes | None = None,
        pace: float = 0.0,
        timeout: float = 1.0,
    ) -> None:
        self._chunks = list(chunks)
        self._chatter = chatter  # sent forever once the script runs out (a talkative frame)
        self._pace = pace
        self._timeout = timeout
        self.sent: list[bytes] = []

    def recv(self, _size: int) -> bytes:
        if self._timeout == 0.0:
            # Non-blocking: a real socket reports "nothing pending" rather than handing over the
            # bytes a later blocking read is waiting for. drain() relies on this.
            raise BlockingIOError("nothing waiting")
        if self._chunks:
            return self._chunks.pop(0)
        if self._chatter is not None:
            if self._pace:
                time.sleep(self._pace)
            return self._chatter
        raise TimeoutError("timed out")

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def settimeout(self, t: float) -> None:
        self._timeout = t

    def setsockopt(self, *_a: object) -> None: ...
    def close(self) -> None: ...


class _PendingSocket(_ScriptedSocket):
    """A socket that hands over its bytes even when polled non-blocking — i.e. data IS waiting."""

    def recv(self, size: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        raise BlockingIOError("drained")


def _unrelated() -> bytes:
    """A message that is NOT what a waiter is waiting for."""
    from memento_core.protocol import encode_reply

    return encode_reply(T_TRANSFER_FILE, 99, cid=1)


def test_wait_for_is_bounded_even_while_the_frame_keeps_talking() -> None:
    """#72: every unrelated message resets the socket timeout, so an unbounded wait_for never ends
    against a frame that chats each ~21s tick — that hung a whole import with no error."""
    from memento_core.control import ControlChannel
    from memento_core.protocol import Ports

    channel = ControlChannel("h", Ports(), timeout=0.3)
    # A frame that never stops talking, and never says the one thing we're waiting for.
    channel._sock = _ScriptedSocket([], chatter=_unrelated(), pace=0.01)  # type: ignore[assignment]

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        channel.wait_for(T_TRANSFER_FILE, [1, 4])
    assert time.monotonic() - started < 5.0  # bounded, not "until the frame stops talking"


def test_download_keeps_its_bytes_when_the_frame_never_confirms() -> None:
    """The closing handshake is a confirmation; the data is already in hand, so a frame that goes
    quiet must not cost us the download (#72)."""
    from memento_core.client import FrameClient
    from memento_core.protocol import Ports, Transfer, encode_reply

    client = FrameClient("h", ports=Ports(), timeout=0.3, file_timeout=0.3)
    started = int(Transfer.GetAlbums) + 1
    client.control._sock = _ScriptedSocket(  # type: ignore[assignment]
        [encode_reply(T_TRANSFER_FILE, started, file_size=5, cid=1)]
    )
    client.file._sock = _ScriptedSocket([b"hello"])  # type: ignore[assignment]

    assert client._download(Transfer.GetAlbums, "albums.json") == b"hello"


def test_download_of_a_file_the_frame_declines_returns_empty_rather_than_hanging() -> None:
    """Firmware 6.02 answers ReadFile with file_size=0 for a stored photo. That must surface as
    'no bytes' for the caller to record, not as a wait that never ends (#72)."""
    from memento_core.client import FrameClient
    from memento_core.protocol import Ports, Transfer, encode_reply

    client = FrameClient("h", ports=Ports(), timeout=0.3, file_timeout=0.3)
    client.control._sock = _ScriptedSocket(  # type: ignore[assignment]
        [encode_reply(T_TRANSFER_FILE, int(Transfer.ReadFile) + 1, file_size=0, cid=1)]
    )
    client.file._sock = _ScriptedSocket([])  # type: ignore[assignment]

    started = time.monotonic()
    assert client._download(Transfer.ReadFile, "photo.jpg") == b""
    assert time.monotonic() - started < 5.0


def test_photo_path_makes_readfile_names_absolute() -> None:
    """ReadFile hands its name straight to File.Open on the frame, so a bare filename never
    resolves — unlike the thumbnail path, which goes through the app's AppendImageDir (#72)."""
    from memento_core.client import PHOTO_DIR, photo_path

    assert photo_path("a.jpg") == PHOTO_DIR + "a.jpg"
    assert photo_path("/mnt/sdcard/Photos/a.jpg") == "/mnt/sdcard/Photos/a.jpg"  # already absolute


def test_download_image_announces_a_size_and_trims_the_over_announcement() -> None:
    """The frame streams from the size the CLIENT announces and never stats the file, so a photo
    read must over-announce and trim at the JPEG end-of-image marker (#72)."""
    from memento_core.client import PHOTO_DIR, FrameClient
    from memento_core.protocol import Ports, Transfer, encode_reply

    photo = b"\xff\xd8" + b"body" * 40 + b"\xff\xd9"
    client = FrameClient("h", ports=Ports(), timeout=0.3, file_timeout=0.3)
    client.control._sock = _ScriptedSocket(  # type: ignore[assignment]
        [encode_reply(T_TRANSFER_FILE, int(Transfer.ReadFile) + 1, file_size=999999, cid=1)]
    )
    client.file._sock = _ScriptedSocket([photo])  # type: ignore[assignment]

    got = client.download_image("holiday.jpg")
    assert got == photo  # trimmed back to the real file, not the announced 999999

    # The request is DES-encrypted into m_Data, so check what the frame would actually read.
    import json as _json

    from memento_core import crypto

    raw = _json.loads(b"".join(client.control._sock.sent).split(b"|")[1])  # type: ignore[attr-defined]
    request = _json.loads(crypto.des_decrypt(raw["m_Data"]))
    assert request["filesize"] != "0"  # zero aborts: "Error: File size is 0 byte"
    assert request["dstfilename"] == PHOTO_DIR + "holiday.jpg"  # absolute, or File.Open fails


def test_recv_until_idle_stops_when_the_frame_goes_quiet() -> None:
    """No protocol call reports a photo's true length, so the read ends on silence."""
    from memento_core.protocol import Ports
    from memento_core.transfer import FileChannel

    ch = FileChannel("h", Ports(), timeout=0.2)
    ch._sock = _ScriptedSocket([b"abc", b"def"])  # type: ignore[assignment]
    assert ch.recv_until_idle(1_000_000) == b"abcdef"


def test_download_survives_the_frame_hanging_up_on_the_closing_handshake() -> None:
    """The frame drops its control session about every 21s (#71). The closing exchange is only an
    acknowledgement — every byte is already in hand — so a teardown there must not lose a completed
    download. Catching only TimeoutError failed every photo of a bulk pull (#72)."""
    from memento_core.client import FrameClient
    from memento_core.protocol import Ports, Transfer, encode_reply

    photo = b"\xff\xd8" + b"x" * 200 + b"\xff\xd9"

    class _DiesOnEnded(_ScriptedSocket):
        def sendall(self, data: bytes) -> None:
            if not self._chunks:  # the Started reply has been consumed: this is ReadFileEnded
                raise ConnectionError("control channel closed by peer")
            super().sendall(data)

    client = FrameClient("h", ports=Ports(), timeout=0.3, file_timeout=0.3)
    client.control._sock = _DiesOnEnded(  # type: ignore[assignment]
        [encode_reply(T_TRANSFER_FILE, int(Transfer.ReadFile) + 1, file_size=999999, cid=1)]
    )
    client.file._sock = _ScriptedSocket([photo])  # type: ignore[assignment]

    assert client.download_image("a.jpg") == photo  # kept, despite the hang-up


def test_a_photo_read_discards_leftovers_from_an_aborted_transfer() -> None:
    """The transfer channel has no framing, so leftovers from an aborted download would be read as
    the head of the next photo. It is drained, not reconnected: the frame sends on the FIRST socket
    in its own list, so reopening would leave it writing to the socket we abandoned (#72)."""
    from memento_core.protocol import Ports
    from memento_core.transfer import FileChannel

    ch = FileChannel("h", Ports(), timeout=0.3)
    ch._sock = _PendingSocket([b"stale bytes from a truncated photo"])  # type: ignore[assignment]
    assert ch.drain() == 34
    assert ch._sock is not None  # still connected — the frame is still holding this socket
    assert ch.drain() == 0  # nothing left

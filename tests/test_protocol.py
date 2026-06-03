"""Control-message codec: framing, encryption of data fields, and the reply envelope."""

from __future__ import annotations

import json

from memento_core import crypto
from memento_core.protocol import (
    EOF,
    T_CHANGE_SETUP,
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

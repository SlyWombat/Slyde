"""Cipher correctness: round-trips, determinism, and broadcast parsing."""

from __future__ import annotations

import json

from memento_core import crypto
from memento_core.discovery import parse_broadcast


def test_aes_round_trip() -> None:
    for s in ["", "hello", "MEMENTO_SMARTFRAME|{}|x", "unicode: café ☃"]:
        assert crypto.aes_decrypt(crypto.aes_encrypt(s)) == s


def test_des_round_trip() -> None:
    for s in ["{}", '{"a":1}', "café ☃", "x" * 1000]:
        assert crypto.des_decrypt(crypto.des_encrypt(s)) == s


def test_ciphers_are_deterministic() -> None:
    # Fixed key/IV/salt -> stable ciphertext (important for golden fixtures/regressions).
    assert crypto.des_encrypt('{"k":"v"}') == crypto.des_encrypt('{"k":"v"}')
    assert crypto.aes_encrypt("frame") == crypto.aes_encrypt("frame")


def test_maybe_des_decrypt_passthrough_for_plain_json() -> None:
    assert crypto.maybe_des_decrypt('{"already":"json"}') == '{"already":"json"}'
    assert crypto.maybe_des_decrypt("") == ""


def test_maybe_des_decrypt_decrypts_ciphertext() -> None:
    ct = crypto.des_encrypt('{"DateTime":"x"}')
    assert json.loads(crypto.maybe_des_decrypt(ct)) == {"DateTime": "x"}


def test_parse_broadcast_plaintext() -> None:
    body = (
        "MEMENTO_SMARTFRAME|"
        + json.dumps(
            {
                "name": "Living Room",
                "softver": "6.02",
                "hardver": "1",
                "size": "35",
                "orientation": "Landscape",
                "ip": "192.168.1.5",
                "mac": "aa:bb",
                "guid": "g",
            }
        )
        + "|<EOF>"
    )
    info = parse_broadcast(body)
    assert info is not None and info.valid
    assert info.name == "Living Room" and info.softver == 6.02 and info.size == 35


def test_parse_broadcast_encrypted() -> None:
    body = (
        "MEMENTO_SMARTFRAME|"
        + json.dumps(
            {
                "name": "N",
                "softver": "6",
                "hardver": "1",
                "size": "35",
                "orientation": "Landscape",
                "ip": "10.0.0.9",
            }
        )
        + "|<EOF>"
    )
    info = parse_broadcast(crypto.aes_encrypt(body))
    assert info is not None and info.ip == "10.0.0.9"

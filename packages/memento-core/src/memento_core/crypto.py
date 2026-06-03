"""Ciphers used by the Memento frame protocol, ported verbatim from ``Cadre.Utils``.

Two ciphers, both with .NET ``Unicode`` (UTF-16LE) plaintext and Base64 ciphertext:

* **AES-256-CBC** — the UDP discovery broadcast payload ("secure" frames).
* **DES-CBC** — command data sub-payloads (``FastEncrypt``/``FastDecrypt``), applied when the
  frame software version is >= ``ENCRYPT_VERSION`` (5). Production frames (6.x) use this.

The keys below are extracted from the official client; they are part of the device protocol,
not secrets we choose. See ``docs/protocol.md``.
"""

from __future__ import annotations

import base64
import warnings

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

try:  # cryptography >= 43 relocated the (legacy but valid) DES/TripleDES primitive
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
except ImportError:  # pragma: no cover - older cryptography
    from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES

# --- AES (discovery broadcast) -----------------------------------------------
# .NET: Rfc2898DeriveBytes(password="otnemeM", salt="emarftramS"), 1000 iters, HMAC-SHA1.
_AES_PASSWORD = b"otnemeM"  # "Memento" reversed
_AES_SALT = bytes([101, 109, 97, 114, 102, 116, 114, 97, 109, 83])  # "emarftramS"
_AES_ITERATIONS = 1000


def _aes_key_iv() -> tuple[bytes, bytes]:
    """Derive (key32, iv16) from one continuous PBKDF2 stream, matching .NET's GetBytes()."""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA1(), length=48, salt=_AES_SALT, iterations=_AES_ITERATIONS)
    material = kdf.derive(_AES_PASSWORD)
    return material[:32], material[32:48]


def aes_encrypt(plaintext: str) -> str:
    key, iv = _aes_key_iv()
    padder = sym_padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-16-le")) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")


def aes_decrypt(ciphertext: str) -> str:
    key, iv = _aes_key_iv()
    raw = base64.b64decode(ciphertext.replace(" ", "+"))
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = dec.update(raw) + dec.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-16-le")


# --- DES (command data sub-payloads) -----------------------------------------
_DES_KEY = b"M3m3nt0 "  # 4D 33 6D 33 6E 74 30 20
_DES_IV = b"UHDFram3"  # 55 48 44 46 72 61 6D 33


def _des() -> TripleDES:
    # TripleDES with an 8-byte key is single-DES (K1=K2=K3) — matches .NET DES.Create().
    # The frame's protocol mandates single-DES, so we deliberately accept (and silence) the
    # library's deprecation warning here rather than break wire compatibility with the device.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*Single-key TripleDES.*")
        return TripleDES(_DES_KEY)


def des_encrypt(plaintext: str) -> str:
    padder = sym_padding.PKCS7(64).padder()
    data = padder.update(plaintext.encode("utf-16-le")) + padder.finalize()
    enc = Cipher(_des(), modes.CBC(_DES_IV)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")


def des_decrypt(ciphertext: str) -> str:
    raw = base64.b64decode(ciphertext)
    dec = Cipher(_des(), modes.CBC(_DES_IV)).decryptor()
    padded = dec.update(raw) + dec.finalize()
    unpadder = sym_padding.PKCS7(64).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-16-le")


def maybe_des_decrypt(field: str) -> str:
    """Frame convention: a data field is plaintext JSON iff it starts with ``{``, else DES."""
    if not field or field.startswith("{"):
        return field
    try:
        return des_decrypt(field)
    except Exception:
        return field


def maybe_aes_decrypt(text: str) -> str:
    """File convention (album/current-album): plaintext JSON iff it starts with ``{``, else AES."""
    if not text or text.lstrip().startswith("{"):
        return text
    try:
        return aes_decrypt(text)
    except Exception:
        return text

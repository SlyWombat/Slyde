"""Compare the OTA package's signing certificate with the device trust store.

No openssl/java on this box, so the DER is walked directly: enough ASN.1 to
pull the certificate out of a PKCS#7 SignedData and print its identity.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

OID_NAMES = {
    "2.5.4.3": "CN",
    "2.5.4.6": "C",
    "2.5.4.7": "L",
    "2.5.4.8": "ST",
    "2.5.4.10": "O",
    "2.5.4.11": "OU",
    "1.2.840.113549.1.9.1": "emailAddress",
    "1.2.840.113549.1.1.1": "rsaEncryption",
    "1.2.840.113549.1.1.5": "sha1WithRSAEncryption",
    "1.2.840.113549.1.1.11": "sha256WithRSAEncryption",
    "1.2.840.113549.1.7.2": "signedData",
    "1.2.840.10045.2.1": "ecPublicKey",
}


def parse_tlv(data: bytes, off: int = 0):
    """Return (tag, header_len, length, value_offset) for the TLV at off."""
    tag = data[off]
    n = data[off + 1]
    if n < 0x80:
        return tag, 2, n, off + 2
    k = n & 0x7F
    length = int.from_bytes(data[off + 2 : off + 2 + k], "big")
    return tag, 2 + k, length, off + 2 + k


def children(data: bytes, off: int, end: int):
    while off < end:
        tag, hl, ln, vo = parse_tlv(data, off)
        yield tag, off, vo, vo + ln
        off = vo + ln


def decode_oid(v: bytes) -> str:
    out = [str(v[0] // 40), str(v[0] % 40)]
    n = 0
    for b in v[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            out.append(str(n))
            n = 0
    return ".".join(out)


def find_certificates(data: bytes) -> list[bytes]:
    """Every DER Certificate in the blob, found structurally.

    A Certificate is SEQUENCE { tbs SEQUENCE { [0] version, INTEGER serial,
    ... } , AlgorithmIdentifier, BIT STRING }; the [0]-then-INTEGER opening of
    the tbs is distinctive enough to key on.
    """
    found = []
    for i in range(len(data) - 16):
        if data[i] != 0x30 or data[i + 1] < 0x80:
            continue
        try:
            tag, hl, ln, vo = parse_tlv(data, i)
        except IndexError:
            continue
        if vo + ln > len(data) or ln < 200:
            continue
        try:
            tag2, hl2, ln2, vo2 = parse_tlv(data, vo)
        except IndexError:
            continue
        if tag2 != 0x30 or vo2 + ln2 > len(data):
            continue
        if data[vo2] != 0xA0:  # [0] EXPLICIT version
            continue
        found.append(data[i : vo + ln])
    # drop nested duplicates
    uniq, seen = [], set()
    for c in found:
        h = hashlib.sha256(c).hexdigest()
        if h not in seen:
            seen.add(h)
            uniq.append(c)
    return uniq


def describe(cert: bytes) -> dict:
    tag, hl, ln, vo = parse_tlv(cert, 0)
    tbs = list(children(cert, vo, vo + ln))[0]
    _, tbs_off, tbs_vo, tbs_end = tbs
    fields = list(children(cert, tbs_vo, tbs_end))
    # version[0], serial, sigalg, issuer, validity, subject, spki
    serial = int.from_bytes(cert[fields[1][2] : fields[1][3]], "big")
    issuer = rdn(cert, fields[3])
    validity = [
        cert[c[2] : c[3]].decode("latin1")
        for c in children(cert, fields[4][2], fields[4][3])
    ]
    subject = rdn(cert, fields[5])
    spki = cert[fields[6][1] : fields[6][3]]
    keyalg = ""
    keybits = 0
    for t, o, v, e in children(cert, fields[6][2], fields[6][3]):
        if t == 0x30:
            for t2, o2, v2, e2 in children(cert, v, e):
                if t2 == 0x06:
                    keyalg = OID_NAMES.get(decode_oid(cert[v2:e2]), decode_oid(cert[v2:e2]))
        elif t == 0x03:  # BIT STRING
            inner = cert[v + 1 : e]
            for t2, o2, v2, e2 in children(inner, 0, len(inner)):
                if t2 == 0x30:
                    mod = next(children(inner, v2, e2))
                    keybits = (mod[3] - mod[2] - 1) * 8
                    break
    return {
        "serial": serial,
        "issuer": issuer,
        "subject": subject,
        "not_before": validity[0] if validity else "",
        "not_after": validity[1] if len(validity) > 1 else "",
        "key": f"{keyalg} {keybits} bit",
        "sha256": hashlib.sha256(cert).hexdigest(),
        "spki_sha256": hashlib.sha256(spki).hexdigest(),
        "der_len": len(cert),
    }


def rdn(cert: bytes, field) -> str:
    _, off, vo, end = field
    parts = []
    for _, o, v, e in children(cert, vo, end):  # SET
        for _, o2, v2, e2 in children(cert, v, e):  # SEQUENCE
            kids = list(children(cert, v2, e2))
            oid = decode_oid(cert[kids[0][2] : kids[0][3]])
            val = cert[kids[1][2] : kids[1][3]].decode("latin1")
            parts.append(f"{OID_NAMES.get(oid, oid)}={val}")
    return ", ".join(parts)


def _pem_to_der(data: bytes) -> list[bytes]:
    import base64
    out = []
    marker = b"-----BEGIN CERTIFICATE-----"
    start = 0
    while True:
        i = data.find(marker, start)
        if i < 0:
            return out
        j = data.find(b"-----END CERTIFICATE-----", i)
        body = data[i + len(marker) : j]
        out.append(base64.b64decode(b"".join(body.split())))
        start = j + 1


def certs_from(path: Path) -> list[bytes]:
    data = path.read_bytes()
    if data[:2] == b"PK":
        out = []
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                out += certs_from_bytes(z.read(n))
        return out
    return certs_from_bytes(data)


def certs_from_bytes(data: bytes) -> list[bytes]:
    if b"-----BEGIN CERTIFICATE-----" in data:
        return _pem_to_der(data)
    return find_certificates(data)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        p = Path(arg)
        print(f"### {p}")
        for c in certs_from(p):
            for k, v in describe(c).items():
                print(f"    {k:12s} {v}")
            print()

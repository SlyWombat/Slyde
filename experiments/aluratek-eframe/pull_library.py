"""Pull this owner's eFrame library from the Sungale cloud by impersonating the Android app (#9).

You OWN this frame and these photos; this fetches YOUR OWN content from the vendor cloud before it
potentially goes away, and stages it locally — a personal backup/migration, read-only to the cloud.

It replays the exact calls the app makes (recovered from the capture):
  GET  /xiaowooya/api/v1/frame/list?client=aluratek&access_token=...      -> devices + album_id
  POST /xiaowooya/api/v1/album/detail?album_id=<id>&client=aluratek&...   -> photo list
  GET  /e_frame_image/<serial>/<id>.bmp   (full, what the frame downloads)
  GET  /e_frame_image/<serial>/<id>.jpg   (thumbnail, what the app shows)

Credentials are NEVER hardcoded: they are read from the live capture pcap (the access_token query
param) or from env (EFRAME_TOKEN). Staged files + manifest are git-ignored.

Usage:
  python3 pull_library.py            # uses ./capture_wake.pcap for creds, stages into ./staging
  EFRAME_TOKEN=... python3 pull_library.py
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST = "us.xiaowooya.eframe.sungale.com.cn"
PORT = 8080
BASE = f"http://{HOST}:{PORT}"
API = f"{BASE}/xiaowooya/api/v1"
CLIENT = "aluratek"
STAGING = HERE / "staging"
UA = "Dart/3.10 (dart:io)"


# --- pull creds out of the capture, without printing them --------------------
def _inner_pcap(zip_or_pcap: Path) -> bytes:
    raw = zip_or_pcap.read_bytes()
    if raw[:4] == b"PK\x03\x04":  # OPNsense wraps the pcap in a zip
        with zipfile.ZipFile(zip_or_pcap) as z:
            name = next(n for n in z.namelist() if n.endswith(".pcap"))
            return z.read(name)
    return raw


def _http_requests(pcap: bytes) -> list[str]:
    """Reassemble client->server TCP streams and return their HTTP request heads (text)."""
    import collections

    off = 24
    streams: dict[tuple, list[tuple[int, bytes]]] = collections.defaultdict(list)
    while off + 16 <= len(pcap):
        _, _, incl, _ = struct.unpack("<IIII", pcap[off : off + 16])
        off += 16
        pkt = pcap[off : off + incl]
        off += incl
        if len(pkt) < 14 or struct.unpack(">H", pkt[12:14])[0] != 0x0800:
            continue
        l3 = pkt[14:]
        ihl = (l3[0] & 0x0F) * 4
        if l3[9] != 6:
            continue
        src = ".".join(map(str, l3[12:16]))
        dst = ".".join(map(str, l3[16:20]))
        tcp = l3[ihl:]
        sp, dp = struct.unpack(">HH", tcp[0:4])
        seq = struct.unpack(">I", tcp[4:8])[0]
        payload = tcp[(tcp[12] >> 4) * 4 :]
        if payload:
            streams[(src, sp, dst, dp)].append((seq, payload))
    heads = []
    for segs in streams.values():
        segs = sorted(set(segs))
        base = segs[0][0]
        buf = bytearray()
        for s, p in segs:
            i = s - base
            if i < 0:
                continue
            if i > len(buf):
                buf.extend(b"\x00" * (i - len(buf)))
            buf[i : i + len(p)] = p
        if buf[:4] in (b"GET ", b"POST"):
            heads.append(bytes(buf).split(b"\r\n\r\n")[0].decode("latin1", "replace"))
    return heads


def creds_from_capture() -> dict[str, str]:
    token = os.environ.get("EFRAME_TOKEN", "")
    serial = os.environ.get("EFRAME_SERIAL", "")
    album = os.environ.get("EFRAME_ALBUM", "")
    if token and serial and album:
        return {"token": token, "serial": serial, "album": album}
    pcap_path = Path(os.environ.get("EFRAME_PCAP", HERE / "capture_wake.pcap"))
    if not pcap_path.exists():
        sys.exit(f"no creds in env and no capture at {pcap_path}; set EFRAME_TOKEN/SERIAL/ALBUM")
    heads = _http_requests(_inner_pcap(pcap_path))
    blob = "\n".join(heads)
    if not token:
        m = re.search(r"access_token=([0-9a-fA-F]{16,})", blob)
        token = m.group(1) if m else ""
    if not album:
        m = re.search(r"album_id=(\d+)", blob)
        album = m.group(1) if m else ""
    if not serial:
        m = re.search(r"/e_frame_image/([A-Za-z0-9]+)/", blob)
        serial = m.group(1) if m else ""
    if not (token and serial):
        sys.exit("could not recover token+serial from capture; pass EFRAME_TOKEN/SERIAL/ALBUM")
    return {"token": token, "serial": serial, "album": album}


# --- talk to the cloud as the app --------------------------------------------
def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _post(url: str) -> bytes:
    req = urllib.request.Request(
        url, data=b"", headers={"User-Agent": UA, "Accept": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _redact(s: str, token: str) -> str:
    return s.replace(token, "<TOKEN>")


def main() -> int:
    c = creds_from_capture()
    token, serial, album = c["token"], c["serial"], c["album"]
    q = f"client={CLIENT}&access_token={token}"
    print(f"impersonating app for serial {serial} (token <{len(token)} hex chars>, album {album})")

    devices = json.loads(_get(f"{API}/frame/list?{q}"))
    # confirm album_id from the device record if we didn't get one from the capture
    for d in devices.get("list", []):
        if not album and d.get("album"):
            album = str(d["album"]["id"])
        alias = (d.get("frameUser") or {}).get("alias")
        print(f"  device: serial={d.get('serialNumber')} name={alias!r} album={album}")

    listing = json.loads(_post(f"{API}/album/detail?album_id={album}&{q}"))
    photos = listing.get("list", [])
    print(f"  album/detail: {len(photos)} photos")

    out = STAGING / serial
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for p in photos:
        rec = {"id": p.get("id"), "name": p.get("name"), "createDate": p.get("createDate")}
        for kind, key in (("full", "path"), ("thumb", "thumbPath")):
            url = p.get(key)
            if not url:
                continue
            fname = url.rsplit("/", 1)[-1]
            dest = out / fname
            try:
                data = _get(url)
                dest.write_bytes(data)
                rec[kind] = {"file": fname, "bytes": len(data)}
                print(f"    {kind:5s} {fname}  ({len(data)} bytes)")
            except Exception as e:
                rec[kind] = {"file": fname, "error": str(e)}
                print(f"    {kind:5s} {fname}  FAILED: {e}")
        manifest.append(rec)

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nstaged {len(photos)} photos into {out}")
    print("manifest:", out / "manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

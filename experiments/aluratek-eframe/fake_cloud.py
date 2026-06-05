"""Aluratek eFrame — fake-cloud recon/replacement server.

Interoperability tooling for an Aluratek 13.3" ePaper WiFi frame (model AEINK13F,
FCC ID RDUAEINK13F) that you OWN. The frame is a low-power MCU that wakes on a
schedule, phones home to Aluratek's cloud over HTTPS, downloads one image, and
sleeps. Goal: stand in for that cloud so the frame keeps working if the service
dies — fed one-way from your own Immich library, the same as Memento Manager.

Two stages, one server:

1. RECON (default): a catch-all that LOGS every request the frame makes (method,
   path, query, headers, body) to stdout + a JSONL file. You learn the cloud API
   by observing the frame hit this server. Point the frame here via an AdGuard
   Home DNS-rewrite of the captured hostname (see README).

2. REPLACE: once you know which path the frame fetches its image from, fill in
   `_maybe_serve_image()` to return the latest Immich asset bytes for that path.

IMPORTANT — TLS reality: to answer the frame's HTTPS calls you must terminate TLS
with a cert the frame accepts. Cheap IoT frames often DON'T validate certs (any
self-signed cert works) — that's the case this tool is built for. If the frame
properly validates against a fixed CA store you can't modify, MITM fails and the
path is a firmware reflash instead. The recon stage tells you which: if the frame
completes a request against your self-signed cert, it isn't validating. Run behind
a TLS terminator, or with `uvicorn --ssl-keyfile --ssl-certfile` (see README).

Nothing is hardcoded for any one deployment — everything is env-configured.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response

# --- configuration (env only; nothing baked in) ------------------------------
IMMICH_BASE_URL = os.environ.get("IMMICH_BASE_URL", "").rstrip("/")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
IMMICH_ALBUM_ID = os.environ.get("IMMICH_ALBUM_ID", "")  # optional: pin to an album
IMMICH_ASSET_SIZE = os.environ.get("IMMICH_ASSET_SIZE", "preview")  # preview|fullsize
LOG_FILE = Path(os.environ.get("EFRAME_LOG_FILE", "eframe-capture.jsonl"))

app = FastAPI(title="aluratek-eframe fake-cloud", docs_url=None, redoc_url=None)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _log(record: dict[str, Any]) -> None:
    """Append one observed exchange to the JSONL capture file and echo to stdout."""
    line = json.dumps(record, ensure_ascii=False)
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


async def _fetch_latest_immich_image() -> bytes | None:
    """Pull one image from Immich to serve to the frame (READ-ONLY, one-way).

    Returns the newest asset's bytes from the configured album (or the whole
    library if no album is set), or None if Immich isn't configured / reachable.
    Wire this into `_maybe_serve_image` once you know the frame's image path.
    """
    if not (IMMICH_BASE_URL and IMMICH_API_KEY):
        return None
    headers = {"x-api-key": IMMICH_API_KEY, "Accept": "application/json"}
    async with httpx.AsyncClient(base_url=IMMICH_BASE_URL, headers=headers, timeout=30) as cx:
        if IMMICH_ALBUM_ID:
            album = (await cx.get(f"/api/albums/{IMMICH_ALBUM_ID}")).json()
            assets = album.get("assets", [])
        else:
            # newest first; take the most recent IMAGE asset
            resp = await cx.post(
                "/api/search/metadata",
                json={"type": "IMAGE", "order": "desc", "size": 1},
            )
            assets = resp.json().get("assets", {}).get("items", [])
        images = [a for a in assets if a.get("type") == "IMAGE"]
        if not images:
            return None
        asset_id = images[-1]["id"] if IMMICH_ALBUM_ID else images[0]["id"]
        img = await cx.get(f"/api/assets/{asset_id}/thumbnail", params={"size": IMMICH_ASSET_SIZE})
        return img.content if img.status_code == 200 else None


# Real cloud contract recovered from app v1.0.3 (libapp.so). The genuine cloud is
# http://us.xiaowooya.eframe.sungale.com.cn:8080/xiaowooya/api/v1/<endpoint>, plain
# HTTP (no TLS). Point AdGuard Home's DNS rewrite of that host at this server. The
# frame->cloud calls (confirm via one live capture) are almost certainly a subset:
API_BASE = "/xiaowooya/api/v1"
KNOWN_ENDPOINTS = {  # endpoint -> what it does (see FINDINGS.md for the full list)
    "frame/ping": "frame heartbeat / wake check",
    "image_library/list": "the photos to display  <-- serve Immich URLs here",
    "schedule/list": "on/off timing",
    "setting/detail": "device settings (orientation, timing)",
    "callback/init_status": "provisioning state",
}


def _maybe_serve_image(method: str, path: str, headers: dict[str, str]) -> bool:
    """Does this request look like the frame asking for its image library/content?

    Real image delivery is `GET image_library/list` (returns image URLs the frame
    then downloads). Tighten to the exact path + response shape once the live
    capture shows what the frame sends.
    """
    p = path.lower()
    accept = headers.get("accept", "").lower()
    return method in ("GET", "POST") and (
        "image_library/list" in p
        or "image/" in accept
        or p.endswith((".jpg", ".jpeg", ".png", ".bin", ".epd", ".raw"))
        or any(k in p for k in ("image", "photo", "download", "content"))
    )


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def catch_all(full_path: str, request: Request) -> Response:
    """Log every request, then either serve an Immich image or a benign 200/204."""
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    _log(
        {
            "ts": _now(),
            "client": request.client.host if request.client else None,
            "method": request.method,
            "path": "/" + full_path,
            "query": dict(request.query_params),
            "headers": headers,
            "body_len": len(body),
            # body as text if it decodes, else hex (frames often send binary/protobuf)
            "body_text": body.decode("utf-8", "replace") if body else "",
            "body_hex": body.hex() if body and not _is_text(body) else "",
        }
    )

    # REPLACE stage: serve a real image once you've identified the image path.
    if _maybe_serve_image(request.method, "/" + full_path, headers):
        data = await _fetch_latest_immich_image()
        if data is not None:
            return Response(content=data, media_type="image/jpeg")

    # RECON stage: acknowledge so the frame's state machine proceeds and reveals
    # its next call. Empty 200 JSON is the safest generic stand-in.
    return Response(content=b"{}", media_type="application/json", status_code=200)


def _is_text(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


# Run:  uvicorn fake_cloud:app --host 0.0.0.0 --port 443 \
#         --ssl-keyfile key.pem --ssl-certfile cert.pem
# (see README.md for cert generation + AdGuard Home DNS-rewrite wiring)

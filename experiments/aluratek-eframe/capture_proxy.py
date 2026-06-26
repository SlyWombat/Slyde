"""Capture-and-forward proxy for the eFrame cloud (#9) — catch the Android app's uploads locally.

Point the app (and/or frame) at this server via an AdGuard Home DNS rewrite of
``us.xiaowooya.eframe.sungale.com.cn`` -> this host, listening on :8080 (plain HTTP, like the real
cloud). For every request it:

  1. logs method/path/query/headers/body-size to ``capture_proxy.jsonl`` (token redacted),
  2. if it's ``photo/upload``, saves each uploaded file part to ``staging/uploads/`` (this is the
     "catch new images" goal),
  3. **forwards the request unchanged to the real cloud and returns the real response** — so the
     app and the real frame keep working. Non-destructive; nothing is impersonated yet.

This is the interim capture tool from the integration plan (docs/sungale-eframe-integration-plan.md
§6). The end state is ingesting uploads straight into Slyde's SungaleCloudBackend.

Run:
  uv run uvicorn capture_proxy:app --host 0.0.0.0 --port 8080
  # then add an AGH DNS rewrite: us.xiaowooya.eframe.sungale.com.cn -> <this host's IP>
"""

from __future__ import annotations

import json
import re
import time
from email.parser import BytesParser
from email.policy import default as email_default
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response

HERE = Path(__file__).resolve().parent
REAL_CLOUD = "http://us.xiaowooya.eframe.sungale.com.cn:8080"
LOG = HERE / "capture_proxy.jsonl"
UPLOADS = HERE / "staging" / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="eframe capture-and-forward proxy", docs_url=None, redoc_url=None)

_TOKEN = re.compile(r"(access_token=)[0-9a-fA-F]{16,}")


def _redact(s: str) -> str:
    return _TOKEN.sub(r"\1<TOKEN>", s)


def _save_upload_parts(body: bytes, content_type: str) -> list[str]:
    """Extract file parts from a multipart/form-data body and save them; return filenames saved."""
    saved: list[str] = []
    try:
        msg = BytesParser(policy=email_default).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
        )
        for i, part in enumerate(msg.iter_parts()):
            payload = part.get_payload(decode=True)
            if not payload or len(payload) < 512:  # skip tiny text fields (album_id, etc.)
                continue
            ext = "jpg" if payload[:3] == b"\xff\xd8\xff" else "bin"
            name = f"upload_{int(time.time())}_{i}.{ext}"
            (UPLOADS / name).write_bytes(payload)
            saved.append(name)
    except Exception as e:
        saved.append(f"<parse-error: {e}>")
    return saved


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def proxy(path: str, request: Request) -> Response:
    body = await request.body()
    ct = request.headers.get("content-type", "")
    saved: list[str] = []
    if "photo/upload" in path and "multipart/form-data" in ct:
        saved = _save_upload_parts(body, ct)

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "client": request.client.host if request.client else None,
        "method": request.method,
        "path": "/" + path,
        "query": _redact(str(dict(request.query_params))),
        "content_type": ct,
        "body_len": len(body),
        "saved_uploads": saved,
    }
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(_redact(json.dumps(record, ensure_ascii=False)), flush=True)

    # Forward upstream unchanged (preserve host header so the real cloud routes correctly).
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    fwd_headers["host"] = "us.xiaowooya.eframe.sungale.com.cn:8080"
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as cx:
        upstream = await cx.request(
            request.method,
            f"{REAL_CLOUD}/{path}",
            params=dict(request.query_params),
            headers=fwd_headers,
            content=body,
        )
    passthru = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in ("content-encoding", "transfer-encoding", "connection")
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=passthru)

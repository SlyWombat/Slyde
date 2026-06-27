"""Sungale cloud backend — white-label Sungale ePaper/WiFi frames (e.g. Aluratek eFrame).

These frames are NOT LAN devices: they poll a vendor cloud over plain HTTP and pull images. We
*impersonate that cloud* (via an AdGuard Home DNS rewrite of the cloud host to this server) and feed
images from Immich, one-way/read-only — the same principle as the LAN backend, but at the cloud
layer. The frame downloads already-prepared images (e-ink palette + dither, #19) from the cache.

The cloud contract was first recovered by static APK analysis, then **confirmed on the wire** by a
live capture of the app talking to the real cloud (see ``experiments/aluratek-eframe/FINDINGS.md``
and ``docs/sungale-eframe-integration-plan.md``). This backend now implements the *observed* shapes:

- host ``us.xiaowooya.eframe.sungale.com.cn:8080``, plain HTTP, base ``/xiaowooya/api/v1``;
- auth is a ``?access_token=`` query param (account-wide) — frames are keyed by serial, not token;
- list endpoints return the payload **at top level** (``{"list": [...]}``), not wrapped in ``data``;
- action endpoints return ``{"code": "ok", "message": ...}`` (``code`` is a string);
- photos carry **two URLs**: ``path`` (the full ``.bmp`` the frame downloads) and ``thumbPath``
  (the ``.jpg`` the app shows), both served from ``/e_frame_image/<serial>/<id>.<ext>``.

Frame family (#14): the backend is host-agnostic — it answers whatever the frame polls, so the same
code serves any regional Sungale rebrand once that host is rewritten here.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.datastructures import UploadFile  # the type the form parser yields

from memento_core import FrameInfo, Ports

from ..frame import Frame
from ..imagecache import ImageCache
from ..serving import resolve_or_register_served_frame
from ..uploads import ingest_upload
from .base import FrameCapabilities, ServedFrameBackend

# Recovered cloud contract (see docs/sungale-eframe-integration-plan.md).
CLOUD_HOST = "us.xiaowooya.eframe.sungale.com.cn"
CLOUD_PORT = 8080
API_BASE = "/xiaowooya/api/v1"
IMAGE_BASE = "/e_frame_image"  # the frame downloads images from here (outside API_BASE)

_log = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"(access_token=)[^&\s]+")


def _redact(text: str) -> str:
    return _TOKEN_RE.sub(r"\1<redacted>", text)


# Device-record constants for the Sungale eFrame family this backend serves (the frame doesn't
# appear to validate them). Mutable per-frame settings (wake interval, orientation, …) are persisted
# in the store and changed via setting/update*; see store.get_frame_setting.
_MODEL_NUMBER = "AEINK13F"
_SCREEN_MODEL = "EL133UF1"  # the 13.3" Spectra-6 panel
_DEFAULT_WAKE_INTERVAL = 259200  # 3 days, if a frame has no setting yet
_ACTION_DISPLAY = 2  # there's a new image to fetch + display
_ACTION_IDLE = 0  # nothing to do (heartbeat) — lets the frame go back to sleep


def _ok(message: str = "ok") -> dict[str, Any]:
    """The vendor's action-result envelope: a string ``code`` + ``message`` (observed shape)."""
    return {"code": "ok", "message": message}


def _setting_block(s: dict[str, str]) -> dict[str, Any]:
    """The camelCase ``setting`` block the app reads, from the stored (snake_case) settings."""
    return {
        "slideShowInterval": s["slide_show_interval"],
        "wakeUpInterval": s["wake_up_interval"],
        "slideShowSwitch": int(s["slide_show_switch"]),
        "displayOrientation": int(s["display_orientation"]),
        "timingType": int(s["timing_type"]),
    }


async def _device_id(request: Request) -> str:
    """The frame identifies itself by ``device_id`` in the form body of dev/* + callback calls."""
    form = await request.form()
    dev = form.get("device_id")
    if not dev or isinstance(dev, UploadFile):
        raise HTTPException(status_code=400, detail="missing device_id")
    return str(dev)


def _media_type(filename: str, data: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "bmp":
        return "image/bmp"
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext == "png" or data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"BM":
        return "image/bmp"
    return "image/jpeg"


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0] if "." in name else name


def _photo_items(frame: Frame, cache: ImageCache, base: str) -> list[dict[str, Any]]:
    """The frame's photos as the vendor list items: a full ``path`` (.bmp) + ``thumbPath`` (.jpg).

    Both URLs resolve back to the same cached image via ``/e_frame_image/<serial>/<stem>.<ext>``.
    """
    items: list[dict[str, Any]] = []
    for key in cache.keys(frame.id):
        stem = _stem(key)
        url = f"{base}{IMAGE_BASE}/{frame.id}/{stem}"
        items.append(
            {
                "id": stem,
                "name": key,
                "createDate": "",
                "path": f"{url}.bmp",
                "thumbPath": f"{url}.jpg",
            }
        )
    return items


def _resolve_key(cache: ImageCache, serial: str, filename: str) -> str | None:
    """Map a requested image filename (``<stem>.bmp``/``.jpg``) back to its cache key."""
    want = _stem(filename)
    for key in cache.keys(serial):
        if key == filename or _stem(key) == want:
            return key
    return None


class SungaleCloudBackend(ServedFrameBackend):
    name = "sungale-cloud"
    capabilities = FrameCapabilities(
        interaction="served",  # the frame polls a server we run; we never connect to it
        transport="cloud",
        color_model="epaper",  # Spectra-6 e-ink: needs palette + dither (see processing.py)
        discovery=False,  # cloud frames register by frame-code, not LAN broadcast
        albums=False,
        thumbnails=False,
        upload=True,  # photos are pushed to the (impersonated) cloud
        delete=True,
        ota=False,  # firmware/OTA path not yet characterized (see #12)
    )

    def discover(self, *, timeout: float = 4.0, ports: Ports | None = None) -> list[FrameInfo]:
        # Cloud frames are not discoverable on the LAN; they reach out to the cloud themselves.
        return []

    def identify(self, request: Request) -> str | None:
        """Resolve the polling frame's stable key (its serial), independent of the account token.

        The ``access_token`` query param is account-wide (shared across an owner's frames), so it is
        NOT the frame key. We key by the frame's serial / device id, accepting it from a query param
        or header. ``X-Frame-Code`` / bearer are kept so onboarding + tests can name a frame.
        """
        # NOTE: the app refers to one frame by THREE ids depending on endpoint — numeric frame id
        # (frame_id / setting_id), device_id, and serial — while the frame uses device_id. Until a
        # canonical identity map unifies them, we accept whichever is present (device_id preferred).
        q = request.query_params
        for key in (
            "serial",
            "sn",
            "serialNumber",
            "device_id",
            "deviceId",
            "frame_id",
            "setting_id",
        ):
            if q.get(key):
                return q[key]
        code = request.headers.get("x-frame-code")
        if code:
            return code
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() or None
        return None

    def _frame_from(self, request: Request, *, code: str | None = None) -> Frame:
        code = code or self.identify(request)
        if not code:
            raise HTTPException(status_code=401, detail="frame not identified")
        return resolve_or_register_served_frame(request.app.state.store, self.name, code)

    def _frame_record(
        self, frame: Frame, cache: ImageCache, setting: dict[str, str]
    ) -> dict[str, Any]:
        """The device record the app/frame reads from ``frame/list`` (observed shape)."""
        return {
            "id": frame.id,
            "deviceId": frame.id,
            "serialNumber": frame.id,
            "modelNumber": _MODEL_NUMBER,
            "screenModel": _SCREEN_MODEL,
            "setting": _setting_block(setting),
            "album": {"id": frame.id, "name": frame.id, "total": len(cache.keys(frame.id))},
            "frameUser": {"alias": frame.name or frame.id},  # the display name the owner set
        }

    def router(self) -> APIRouter:
        """The HTTP surface the frame polls, mounted at the cloud's paths."""
        router = APIRouter(tags=["sungale-frame"])

        @router.api_route(f"{API_BASE}/user/login", methods=["POST"])
        async def user_login(request: Request) -> dict[str, Any]:
            # The frame/app authenticates; we issue a token == its frame-code so later calls map
            # back to the same frame. Identity comes from a header/query or a body identifier.
            code = self.identify(request)
            if not code:
                body = await _json(request)
                for k in ("frame_id", "frameId", "sn", "serial", "device_id", "code", "username"):
                    if body.get(k):
                        code = str(body[k])
                        break
            if not code:
                raise HTTPException(status_code=400, detail="no frame identifier in login")
            frame = resolve_or_register_served_frame(request.app.state.store, self.name, code)
            return {"code": "ok", "access_token": frame.id, "accessToken": frame.id}

        @router.api_route(f"{API_BASE}/frame/ping", methods=["GET", "POST"])
        async def frame_ping(request: Request) -> dict[str, Any]:
            self._frame_from(request)
            return _ok()

        @router.api_route(f"{API_BASE}/frame/list", methods=["GET", "POST"])
        async def frame_list(request: Request) -> dict[str, Any]:
            frame = self._frame_from(request)
            store = request.app.state.store
            record = self._frame_record(
                frame, request.app.state.image_cache, store.get_frame_setting(frame.id)
            )
            return {"list": [record]}

        @router.api_route(f"{API_BASE}/setting/detail", methods=["GET", "POST"])
        async def setting_detail(request: Request) -> dict[str, Any]:
            frame = self._frame_from(request)
            return _setting_block(request.app.state.store.get_frame_setting(frame.id))

        @router.api_route(f"{API_BASE}/setting/update", methods=["GET", "POST"])
        async def setting_update(request: Request) -> dict[str, Any]:
            # The app changes device settings (wake interval, slideshow, orientation, …) — persist
            # whichever fields are present so dev/frame/status + setting/detail reflect them.
            frame = self._frame_from(request)
            q = request.query_params
            request.app.state.store.set_frame_setting(
                frame.id,
                wake_up_interval=q.get("wake_up_interval"),
                slide_show_interval=q.get("slide_show_interval"),
                slide_show_switch=q.get("slide_show_switch"),
                display_orientation=q.get("display_orientation"),
                timing_type=q.get("timing_type"),
            )
            return {"code": "ok", "message": "update setting successfully."}

        @router.api_route(f"{API_BASE}/setting/update_display_orientation", methods=["GET", "POST"])
        async def setting_update_orientation(request: Request) -> dict[str, Any]:
            # The dedicated orientation endpoint (decompile) — persist display_orientation.
            frame = self._frame_from(request)
            request.app.state.store.set_frame_setting(
                frame.id, display_orientation=request.query_params.get("display_orientation")
            )
            return {"code": "ok", "message": "update setting successfully."}

        @router.api_route(f"{API_BASE}/setting/update_timing_type", methods=["GET", "POST"])
        async def setting_update_timing_type(request: Request) -> dict[str, Any]:
            frame = self._frame_from(request)
            request.app.state.store.set_frame_setting(
                frame.id, timing_type=request.query_params.get("timing_type")
            )
            return {"code": "ok", "message": "update setting successfully."}

        @router.api_route(f"{API_BASE}/schedule/list", methods=["GET", "POST"])
        async def schedule_list(request: Request) -> dict[str, Any]:
            self._frame_from(request)
            return {"list": []}

        @router.api_route(f"{API_BASE}/photo/upload", methods=["POST"])
        async def photo_upload(request: Request) -> dict[str, Any]:
            # The app pushes a photo (multipart): album_id identifies the frame, plus the image.
            form = await request.form()
            album_id = form.get("album_id") or request.query_params.get("album_id")
            frame = self._frame_from(request, code=str(album_id) if album_id else None)
            upload = next((v for v in form.values() if isinstance(v, UploadFile)), None)
            if upload is None:
                raise HTTPException(status_code=400, detail="no image in upload")
            try:
                data = await upload.read()
            finally:
                await upload.close()
            state = request.app.state
            await ingest_upload(
                frame=frame,
                data=data,
                settings=state.settings,
                image_cache=state.image_cache,
                asset_previews=state.asset_previews,
                uploads=state.uploads,
                library=state.library,
                store=state.store,
            )
            return _ok("Upload photos successfully")

        # --- the FRAME's own API (ESP32, form-urlencoded, keyed by device_id) -- confirmed by the
        # live wake capture (#9). Distinct from the app endpoints above.
        @router.api_route(f"{API_BASE}/dev/frame/status", methods=["POST"])
        async def dev_frame_status(request: Request) -> dict[str, Any]:
            # The frame's heartbeat: we answer with the current image's change-state. action=2 once
            # (fetch + display) then 0 on the heartbeats, so the frame displays once and sleeps.
            store = request.app.state.store
            frame = resolve_or_register_served_frame(store, self.name, await _device_id(request))
            current = request.app.state.image_cache.current_key(frame.id) or ""
            content_key, last_update_ms, acked_key = store.get_frame_display(frame.id)
            if current != content_key:  # the image changed -> new lastUpdate, pending display
                content_key, last_update_ms = current, str(int(time.time() * 1000))
                store.set_frame_display(
                    frame.id,
                    content_key=content_key,
                    last_update_ms=last_update_ms,
                    acked_key=acked_key,
                )
            action = _ACTION_DISPLAY if (content_key and content_key != acked_key) else _ACTION_IDLE
            # Sleep for the configured wake interval (the frame sleeps on the first value), so
            # changing it via setting/update changes the wake cadence.
            try:
                interval = int(store.get_frame_setting(frame.id)["wake_up_interval"])
            except (ValueError, KeyError):
                interval = _DEFAULT_WAKE_INTERVAL
            return {
                "lastUpdate": last_update_ms,
                "action": action,
                "firstImageToDisplay": 0,
                "wakeUpSchedule": [interval, 0],
            }

        @router.api_route(f"{API_BASE}/dev/playlist/detail", methods=["POST"])
        async def dev_playlist_detail(request: Request) -> dict[str, Any]:
            # The frame's playlist: same item shape as the app's album/detail (path .bmp + thumb).
            store = request.app.state.store
            frame = resolve_or_register_served_frame(store, self.name, await _device_id(request))
            base = str(request.base_url).rstrip("/")
            return {"list": _photo_items(frame, request.app.state.image_cache, base)}

        @router.api_route(f"{API_BASE}/callback/action_status", methods=["POST"])
        async def callback_action_status(request: Request) -> dict[str, Any]:
            # The frame acks it displayed the image -> mark current content shown so action is idle.
            store = request.app.state.store
            frame = resolve_or_register_served_frame(store, self.name, await _device_id(request))
            content_key, last_update_ms, _ = store.get_frame_display(frame.id)
            store.set_frame_display(
                frame.id,
                content_key=content_key,
                last_update_ms=last_update_ms,
                acked_key=content_key,
            )
            return {"code": "success", "message": "action code updated succefully."}

        @router.api_route(f"{API_BASE}/callback/power_off", methods=["POST"])
        async def callback_power_off(request: Request) -> dict[str, Any]:
            # The frame is going back to sleep; just acknowledge.
            resolve_or_register_served_frame(
                request.app.state.store, self.name, await _device_id(request)
            )
            return {"code": "success", "message": "power off status sync succefully."}

        @router.api_route(f"{API_BASE}/album/detail", methods=["GET", "POST"])
        async def album_detail(request: Request) -> dict[str, Any]:
            # The app addresses a frame's photo set by album_id (== the frame id we mint in
            # frame/list); fall back to the usual identification if it isn't supplied.
            frame = self._frame_from(request, code=request.query_params.get("album_id"))
            base = str(request.base_url).rstrip("/")
            items = _photo_items(frame, request.app.state.image_cache, base)
            return {"list": items, "total": len(items)}

        @router.api_route(f"{API_BASE}/image_library/list", methods=["GET", "POST"])
        async def image_library_list(request: Request) -> dict[str, Any]:
            # Same photo list as album/detail; kept until the live wake shows which the frame polls.
            frame = self._frame_from(request)
            base = str(request.base_url).rstrip("/")
            items = _photo_items(frame, request.app.state.image_cache, base)
            return {"list": items, "total": len(items)}

        @router.get(IMAGE_BASE + "/{serial}/{filename}")
        async def image_file(serial: str, filename: str, request: Request) -> Response:
            cache: ImageCache = request.app.state.image_cache
            key = _resolve_key(cache, serial, filename)
            data = cache.get(serial, key) if key else None
            if data is None:
                raise HTTPException(status_code=404, detail="image not found")
            etag = f'W/"{len(data)}-{hashlib.sha1(data).hexdigest()[:16]}"'
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers={"ETag": etag})
            return Response(
                content=data, media_type=_media_type(filename, data), headers={"ETag": etag}
            )

        # Catch-all (registered LAST, so the routes above win): log any cloud-API request we don't
        # handle — the app or frame calling an endpoint we haven't reverse-engineered yet. Returns a
        # benign ok so the device keeps going and the gap shows up in the logs. (token redacted.)
        @router.api_route(
            f"{API_BASE}/{{rest:path}}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )
        async def unhandled(rest: str, request: Request) -> dict[str, Any]:
            body = (await request.body())[:400]
            _log.warning(
                "sungale: UNHANDLED %s %s%s%s body=%r",
                request.method,
                request.url.path,
                "?" if request.url.query else "",
                _redact(request.url.query),
                _redact(body.decode("latin1", "replace")),
            )
            return _ok()

        return router

    async def respond(self, frame: Frame, request: Request) -> Response:
        """Serve the frame its current prepared image directly (a convenience / fallback)."""
        data: bytes | None = await request.app.state.frame_delivery.image_for(frame)
        if data is None:
            return Response(status_code=204)
        return Response(content=data, media_type=_media_type("", data))


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}

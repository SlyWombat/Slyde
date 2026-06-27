# eFrame cloud — static-analysis findings (from app v1.0.3)

Extracted from `libapp.so` (Dart AOT) inside the arm64 split of
`com.xiaowooya.aluratek_eframe` v1.0.3. No live capture needed.

## The headline

| | |
|---|---|
| **Real ODM** | **Sungale** (`sungale.com.cn`) — Aluratek eFrame is a Sungale white-label; app shell by "xiaowooya" |
| **Cloud host** | `us.xiaowooya.eframe.sungale.com.cn` (US region) |
| **Transport** | **plain HTTP on port 8080 — NO TLS** |
| **Auth** | Bearer token (`access_token` / `accessToken`, "Access Token Expired") obtained via `user/login` |
| **TLS pinning** | N/A — there's no TLS at all |
| **Image delivery** | API returns image URLs; app's CacheManager downloads them (`.jpg`) |
| **Firmware** | "Firmware Version" + `callback/roll_back_init` + ROLLBACK concept present (frame-side OTA exists server-side) |

**Revival verdict: GO, the easy way.** Because the frame talks **unencrypted
HTTP** to a known host, there is **no cert/pinning obstacle**. An AdGuard Home DNS
Rewrite of `us.xiaowooya.eframe.sungale.com.cn` → our server (port 8080) lets us
fully impersonate the cloud and feed the frame from Immich. No soldering, no
firmware dump required.

## ⚠️ Privacy finding (worth noting independently)

Your photos and your login/bearer token are sent **in cleartext HTTP** to a
server **in China** (`.com.cn`). Anyone on-path can read them, and the operator
retains them. That's a strong, independent reason to cut this frame over to a
local replacement — exactly what this toolkit does.

## Full app→cloud API (base: `http://us.xiaowooya.eframe.sungale.com.cn:8080/xiaowooya/api/v1`)

**User/auth**
- `POST user/register`, `user/login`, `user/logout`, `user/reset_password`
- `user/verification_code`, `user/delete`

**Frame**
- `frame/list`, `frame/rename`, `frame/ping`, `dev/frame/reset`

**Photos / library**
- `photo/upload`, `photo/delete`, `photo/to_top`
- `image_library/push`, `image_library/list`
- `album/detail`

**Schedule (on/off timing)**
- `schedule/list`, `schedule/add`, `schedule/update`, `schedule/delete`, `schedule/status`

**Settings**
- `setting/detail`, `setting/update`, `setting/update_timezone`
- `setting/update_timing_type`, `setting/update_display_orientation`

**Callbacks (init / rollback — likely frame provisioning + OTA safety)**
- `callback/init_status`, `callback/roll_back_init`

## What we still need the live capture for

The above is the **app→cloud** contract. The **frame→cloud** side (what the
sleeping frame actually polls on wake — almost certainly `frame/ping` +
`image_library/list` + `schedule/list` + `setting/detail` against the same host)
must be confirmed by capturing the frame's own traffic once, so we reimplement
exactly the endpoints the frame calls and the response shapes it expects. The
AdGuard query log will show the wake; the rest we already know.

## Live capture — CONFIRMED (2026-06-26, app push over the gateway)

A passive OPNsense packet-capture (LAN `igc1`, host `47.88.4.176`, **all ports**)
caught a real photo push from the Android app. Everything is **plain HTTP on
:8080**, so the full exchange — including the image — was reconstructed straight
from the pcap. This upgrades the static analysis above from *predicted* to
*observed*. (The pcap holds a live `access_token`, the frame MAC + serial, and
personal photo EXIF/GPS — git-ignored, never committed.)

**The app does ALL image processing client-side; the cloud just stores+serves bytes:**

| | Original (Pixel HDR+) | Uploaded by app |
|---|---|---|
| Dimensions | 3072 × 4080 portrait | **1200 × 1600** |
| Format/size | JPEG, 4.4 MB | **JPEG, RGB, ~139 KB** |

The `photo/upload` body and the cloud's served copy are the **byte-identical
1200×1600 JPEG**. The app does **not** do Spectra-6 quantization/dithering — it
only crops + downscales to a 1200×1600 plain-RGB JPEG. **The 6-colour e-paper
conversion happens on the frame itself.** ⇒ our replacement only has to serve a
1200×1600 JPEG (+ a `.bmp` variant); the frame does the hard part.

**Observed endpoints (base `…:8080/xiaowooya/api/v1`, auth via `?access_token=`):**
- `GET frame/list` → device + `setting` + `album` (full schema below)
- `POST photo/upload` (`multipart/form-data`) → `{"code":"ok","message":"Upload photos successfully"}`
- `GET image_library/list` → photo list, **two URLs each**:
  - `path` → `…/e_frame_image/<serial>/<id>.bmp`  ← **what the frame downloads**
  - `thumbPath` → `…/<id>.jpg`  ← the thumbnail the app shows
- Frame image GETs use `If-None-Match` ETags → `304 Not Modified` (HTTP caching;
  only changed images are re-pulled).

**`frame/list` → `setting` (live values):** `wakeUpInterval: 259200` (**3 days**,
matches the ~2-day DNS cadence), `slideShowInterval: 60`, `firmware: "2.0.26"`,
`displayOrientation: 1`, `modelNumber: "AEINK13F"`, `screenModel: "EL133UF1"`
(the Spectra-6 panel).

## What the wake capture still needs to confirm

App→cloud + the static download URLs are now known. The remaining unknown is the
**frame→cloud** side: does the frame download the **`.bmp`** (likely already
panel-packed / dithered) vs the `.jpg`, and does it hit `callback/init_status` /
a firmware check on wake? The capture is left running for the next ~05:01 UTC wake
to answer exactly that.

## Next steps

1. Stand up `fake_cloud.py` and point the AGH DNS Rewrite at it (HTTP :8080).
2. Await the wake; confirm whether the frame pulls `.bmp` or `.jpg` and its exact
   request order → match response shapes (we already have most of them above).
3. Implement `frame/list` + `image_library/list` to return Immich-backed image
   URLs (`path`/`thumbPath`) served by us; reproduce the **frame-expected** image
   (1200×1600; `.bmp` form TBD from the wake).
4. Read-only, one-way from Immich — same contract as Slyde.

## Frame→cloud — CONFIRMED at the 2026-06-27 05:00:53 UTC wake (the real frame, not the app)

The passive OPNsense capture caught the **frame's own** wake. It is an **ESP32**
(`User-Agent: ESP32 HTTP Client/1.0`), plain HTTP `:8080`, **`x-www-form-urlencoded`**
POST bodies, and identifies by **`device_id`** (a form field). Its API is *different*
from the app's, so the static/app analysis above was the app contract, not the frame's.

Wake sequence (frame → cloud):
1. `POST dev/frame/status` — body `device_id,rssi,battery,fw,p_id,device_mode,t`
   → `{"lastUpdate","action","firstImageToDisplay":0,"wakeUpSchedule":[a,b]}`
   (`action` 2 = fetch+display, 0 = idle; `a+b = 259200` = the 3-day wakeUpInterval).
2. `POST dev/playlist/detail` — body `device_id,t`
   → `{"list":[{id,name,createDate,"path":…<id>.bmp,"thumbPath":…<id>.jpg}]}` (same shape as album/detail).
3. `GET /e_frame_image/<serial>/<id>.bmp` — downloads the **.bmp** (no `If-None-Match`/ETag).
4. `POST callback/action_status` — body `device_id,t,action_code` → `{"code":"success",…}`.
5. ~55× `POST dev/frame/status` (all `action:0`) while the e-paper renders.
6. `POST callback/power_off` — body `device_id` → `{"code":"success",…}`.

It fetched `1782501676382714867.bmp` — the exact image we had pushed — proving the
byte-exact panel BMP and the `path`/`thumbPath` shapes are right, it wants **.bmp**, and
it uses **no ETag**. (The earlier "frame image GETs use ETags" note was the *app's* `.jpg`
thumbnail fetches, not the frame.)

These four endpoints are implemented in `SungaleCloudBackend` (keyed by `device_id`, with a
`frame_display` table driving the `action` 2→0 transition). The eFrame go-live now only needs
the AGH DNS rewrite + publishing the hub's `:8080`.

## More app endpoints sniffed (2026-06-27 setting changes) + an identity caveat

Driving the app's settings revealed the rest of the app→cloud surface (all return the ok envelope):

- `GET setting/update?setting_id=&wake_up_interval=&slide_show_interval=&device_id=&slide_show_switch=` (by `device_id`)
- `POST setting/update_display_orientation?setting_id=&display_orientation=` → `{"code":"ok","message":"update setting successfully."}`
- `POST setting/update_timing_type?setting_id=&timing_type=`
- `POST schedule/add?frame_id=&weekday_array=…&time_set=07%3A00&alias=Every+Day` → `{"code":"ok","message":"Schedule added successfully."}`
- `POST schedule/status?schedule_id=&status=` → `{"code":"ok","message":"update schedule successfully."}`
- `GET setting/detail?frame_id=`

**Identity unification (implemented):** the app refers to ONE frame by **three different ids**
depending on the endpoint — the **numeric frame id** (`frame_id` / `setting_id`, e.g. `753`), the
**`device_id`** (`42ce…`), and the **serial** (`AS54…` in image paths) — while the **frame**
identifies itself only by `device_id`. A **`frame_alias`** table now maps every presented id to one
canonical Frame: each request's ids are gathered (`_candidate_ids`, device_id preferred) and linked
via `resolve_served_frame`, so a request carrying two ids opportunistically links them, and if a
device fragmented into two Frames they are **merged** (`rekey_frame`). App and frame now resolve to a
single Frame regardless of which id they present.

`setting/update`, `setting/update_display_orientation`, `setting/update_timing_type` are implemented
(persisted per frame; drive `wakeUpSchedule` + the setting block). `schedule/*` is currently handled
by the catch-all logger (benign ok) — implement if the on/off schedule is needed.

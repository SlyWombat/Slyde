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

## Next steps

1. Stand up `fake_cloud.py` and point the AGH DNS Rewrite at it (HTTP :8080).
2. Force/await one frame wake; the catch-all logs the **frame's** exact calls +
   request bodies → fill in real response shapes for those endpoints.
3. Implement `image_library/list` to return Immich image URLs (served by us),
   and `frame/ping` / `setting/detail` / `schedule/list` to keep it happy.
4. Read-only, one-way from Immich — same contract as Slyde.

# Sungale / Aluratek eFrame → Slyde integration plan

Status: **plan** (June 2026). Reconciles the existing best-effort `SungaleCloudBackend`
scaffold (commit `33a0f57`, built from *static* APK analysis) with the **live capture**
(`experiments/aluratek-eframe/FINDINGS.md`, #9) and the **real device files** we staged
by impersonating the app (`experiments/aluratek-eframe/pull_library.py`).

The scaffold's own docstring says *"field names are a best effort pending the live
frame→cloud capture."* We now have the wire truth, so this plan is the diff from scaffold
→ correct, plus the new image-format and app-ingestion work.

---

## 1. What the live capture proved (protocol corrections)

All plain HTTP, host `us.xiaowooya.eframe.sungale.com.cn:8080`, base `/xiaowooya/api/v1`.
Auth is a **`?access_token=<32-hex>` query param** on *every* call (+ `?client=aluratek`),
**not** a bearer header. The token is **per-account**, shared across all of an owner's
frames — so it is *not* a per-frame key.

Observed calls (app → cloud):

| Call | Purpose | Response shape (top-level, **no envelope**) |
|---|---|---|
| `GET frame/list?client=&access_token=` | devices + settings + album | `{"list":[{id,deviceId,serialNumber,macAddress,modelNumber,setting{…},album{id,name,total},frameUser{alias},screenModel,…}]}` |
| `POST album/detail?album_id=<id>&…` (empty body) | photo list for a frame | `{"list":[{id,name,createDate,path,thumbPath}]}` |
| `POST photo/upload?…` (multipart) | push a photo | `{"code":"ok","message":"Upload photos successfully"}` |
| `POST frame/ping?device_id=<id>&…` | heartbeat | `{"code":"offline","message":"<deviceId>"}` (offline = frame asleep) |
| `GET /e_frame_image/<serial>/<id>.bmp` | **full image the frame downloads** | binary BMP (see §2) |
| `GET /e_frame_image/<serial>/<id>.jpg` | thumbnail the app shows | binary JPEG; supports `If-None-Match` → `304` |

**Envelope correction:** the scaffold returns `{"code":0,"msg":"ok","data":…}`. The real
cloud returns the payload **at top level** (`{"list":[…]}`) for list calls, and
`{"code":"<string>","message":"…"}` for action calls (`code` is a *string* `"ok"`/`"offline"`,
key is `message`). No `data` wrapper.

**The app does no colour processing.** `photo/upload` carries a plain **1200×1600 RGB JPEG**
(~139 KB) plus form fields `album_id` and `display_orientation`. The **cloud** produces the
e-ink panel file (§2). So in our replacement, *we* own that conversion.

---

## 2. The panel image format (decoded from the real `.bmp` files)

Every full image the frame pulls is **exactly 960,118 bytes** — a fixed-size raw panel buffer:

- **4-bit indexed BMP** (`BM`, 40-byte DIB, `bpp=4`, `compression=0`), **16-entry palette**.
- Stored geometry **600 × 3200** (a packed layout of the 1200×1600 image: two near-duplicate
  600×1600 bands stacked — a gate-driver split of the EL133UF1 panel; exact interleave to be
  pinned by a round-trip test when building the encoder).
- 1200×1600 px × 4 bpp = 960,000 B pixel data + 118 B (54 header + 64 palette).

**Real Spectra-6 palette (pure primaries, replaces the guessed values in `processing.py`):**

| idx | RGB | ink | share of a real photo |
|---|---|---|---|
| 0 | `(0,0,0)` | black | 41.6% |
| 1 | `(255,255,255)` | white | 20.2% |
| 2 | `(255,255,0)` | yellow | 6.0% |
| 3 | `(255,0,0)` | red | 15.2% |
| 4 | `(0,0,0)` | (spare/black, unused) | 0% |
| 5 | `(0,0,255)` | blue | 7.9% |
| 6 | `(0,255,0)` | green | 9.2% |
| 7–15 | `(0,0,0)` | padding | 0% |

The current `SPECTRA6_PALETTE` uses muted approximations (`(200,40,30)`, `(230,200,30)`, …)
and 6 contiguous entries. The **real** panel quantizes to **pure RGB primaries** at the
**index order `0=K,1=W,2=Y,3=R,(4 spare),5=B,6=G`**, padded to 16. Match this exactly for
byte-compatible output (the index order is what the panel controller dereferences).

**Pipeline our backend must reproduce (the cloud's job):**
`upload JPEG → fit to 1200×1600 → quantize to the 6 primaries + Floyd–Steinberg dither →
pack into the 600×3200 4bpp BMP with the 16-entry palette above`.

---

## 3. Multi-frame identity & naming

Yes — the name is carried. In `frame/list`, each device has:

- `deviceId` (e.g. `42ce…0154`) — opaque per-device id; used in `frame/ping?device_id=`.
- `serialNumber` (`AS54S44600647`) — **the image-path key** (`/e_frame_image/<serial>/…`) and
  the album name. The most useful stable, human-traceable frame key.
- `frameUser.alias` (**`"Kazoo Frame"`**) — the **display name** the owner set; maps to `Frame.name`.
- `album.id` (`3501`) — needed to address the frame's photo set in `album/detail`.

Because the **token is account-wide**, `identify()` must key a frame by **`device_id` / `serial`**,
not the token. One account with N frames = one token, N (deviceId, serial, album) tuples.

**Mapping to Slyde's `Frame` (served):** `id = serialNumber` (stable, in the image path),
`frame_code = serialNumber`, `name = frameUser.alias`. This rides the existing served-frame
registry (`resolve_or_register_served_frame`, `capture_name`) and the multi-frame plumbing —
no new identity machinery, just correct field extraction. Multiple frames "just work": each
serial auto-registers on first poll, curation/delivery/scheduling are already keyed by `frame.id`.

---

## 4. Gap analysis — existing `SungaleCloudBackend` → required

| Area | Scaffold today | Live truth → change |
|---|---|---|
| Auth / `identify()` | bearer / `X-Frame-Code` / `?frame_id` | read **`?access_token`** (account) + **`device_id`/serial** (frame); key frame by serial |
| Success envelope | `{"code":0,"msg":"ok","data":…}` | top-level payload for lists; `{"code":"ok","message":…}` for actions |
| `frame/list` | `{"list":[{id,name}]}` | full record: `deviceId, serialNumber, setting{}, album{id,name,total}, frameUser{alias}, screenModel` |
| Photo list endpoint | `image_library/list` → `{id,name,url}` | app: **`POST album/detail?album_id=`** → `{id,name,createDate,path,thumbPath}`. The *frame* uses **`dev/playlist/detail`** (same item shape) — see §8 |
| Image URL fields | `url` | **`path`** (`…/<serial>/<id>.bmp`, full) + **`thumbPath`** (`.jpg`) |
| Image route | `/image_library/file/<frame>/<key>` | **`GET /e_frame_image/<serial>/<id>.{bmp,jpg}`** (outside `API_BASE`), with **ETag / `If-None-Match` → 304** |
| `photo/upload` | *(not implemented)* | **done** — accepts multipart, ingests the pushed photo (§6): prepares the panel BMP for the frame + keeps Slyde's canonical preview |
| `album/detail` | *(not implemented)* | **implement** |
| Image format | epaper → **PNG** (`processing.py`) | **4bpp indexed BMP**, 600×3200 packing, real palette (§2) |
| Palette | muted 6-colour guess | pure primaries, exact index order (§2) |
| `ota` capability | `False` | keep `False` (firmware path uncharacterized, #12) |

---

## 5. Image-pipeline work

1. **Fix `SPECTRA6_PALETTE`** to the real primaries + index order (§2).
2. **Add an e-ink BMP encoder** alongside `prepare()`: emit the 4bpp indexed BMP with the
   16-entry palette and the 600×3200 packing, instead of PNG, when the target panel is this
   family. Drive it from the processing profile (e.g. `color_model="epaper"`,
   `container="bmp-4bpp-600x3200"`), keeping it config-/capability-driven (ADR-009, no hardcoding).
3. **Pin the packing** with a round-trip test: encode a known gradient/test card, compare to a
   cloud-produced `.bmp` of the same input (we can generate one by uploading a test image via §6
   and pulling it back) until byte-identical.

---

## 6. Catching the Android app locally (new uploads)

The app adds photos by `POST photo/upload` (multipart: `album_id`, `display_orientation`, file)
to the cloud host. To capture these locally we point that host at us (AGH DNS rewrite of
`*.eframe.sungale.com.cn` → our server) and handle the upload. Two stages:

- **Now (interim, non-destructive): a capture-and-forward proxy**
  (`experiments/aluratek-eframe/capture_proxy.py`). It receives the app's calls, **saves
  `photo/upload` bodies to `staging/uploads/`**, and **forwards everything to the real cloud**,
  returning the real response — so the app *and* the existing frame keep working while we
  collect real uploads. This is the immediate "catch the app locally" capability.

- **Ingest into Slyde (done).** `SungaleCloudBackend.photo/upload` accepts the multipart push and
  `uploads.ingest_upload` makes the photo Slyde-owned: it prepares the panel BMP into the frame's
  cache (the frame pulls it on next wake via album/detail → `/e_frame_image/...`) and stores a
  canonical preview (served at `/api/assets/{id}/preview`). App-uploaded photos are not in Immich,
  so Slyde owns them (the original is persisted too). At that point the app talks only to Slyde and
  the China cloud is cut over.
- **Unified into curation (done).** An upload is a first-class `library_item` with `source='upload'`:
  it appears in the frame's library next to Immich photos, flows through the delivery queue (delivery
  re-prepares from the persisted original, never Immich), and an Immich "Set library" PUT curates
  alongside it without wiping it (`set_library` only replaces `source='immich'` rows).

We already proved the **pull** direction (impersonate app → download our whole library):
`pull_library.py` staged all 6 photos (full `.bmp` + thumb `.jpg`) + a manifest.

---

## 7. Phased plan

1. **Protocol fixes** to `SungaleCloudBackend` (app endpoints, envelopes, `album/detail`, image route). ✅
2. **Image format**: real palette + byte-exact 4bpp-BMP encoder (`panel_bmp.py`). ✅
3. **App ingestion**: `photo/upload` → library/delivery (unified into curation). ✅
4. **The frame's own API** (§8): `dev/frame/status` + `dev/playlist/detail` + `callback/*`, keyed by
   `device_id`, with the `action` 2→0 display-state. ✅
5. **Multi-backend hub**: `FRAME_SERVED_BACKENDS` so one hub drives Memento + the eFrame. ✅
6. **Cutover** (live, remaining): AGH rewrite of the cloud host → the kdocker2 hub (publish `:8080`),
   then verify a real wake pulls our BMP and renders; confirm multi-frame.
7. **Docs**: `docs/frame-backends.md` + `.env.example`. ✅

## 7a. Previews are Slyde's, not a frame's

Slyde keeps its **own canonical preview per asset**, independent of any managed frame (like Immich
keeps a thumbnail). A preview is a property of the library, not a frame, so it must survive frame
removal, format changes, and offline frames — and the curation UI must not re-fetch Immich every view.

- `previews.py` — `render_canonical_preview` (frame-agnostic: EXIF-upright, fit to a max edge, JPEG)
  + `AssetPreviewCache` (on-disk, **keyed by `asset_id`**, not `frame_id`).
- `GET /api/assets/{asset_id}/preview` — Slyde's own preview; generated lazily on first request,
  then persisted. Served from Slyde's store **even when Immich is down**, and not purged when a frame
  is deregistered (separate from the per-frame prepared-image cache that holds the panel BMP).
- The **frame-specific** render (`GET /api/frames/{id}/preview/{asset}`, "how it looks on this
  panel") layers on top, on demand, and for an e-ink frame emits the viewable palette PNG (not the
  packed panel BMP, which is delivery-only).

## 8. The frame's own API — CAPTURED at the 2026-06-27 05:00:53 UTC wake (#9)

The static analysis + app capture gave us the **app's** contract. The live wake gave us the
**frame's**, which is *different*. The frame is an **ESP32** (`User-Agent: ESP32 HTTP Client/1.0`),
plain HTTP on `:8080`, **`x-www-form-urlencoded`** POST bodies, and it identifies by **`device_id`**
(a form field — not serial, not token, not a query param). Its wake sequence:

| Frame call (POST unless noted) | Body fields | Response |
|---|---|---|
| `dev/frame/status` (heartbeat) | `device_id,rssi,battery,fw,p_id,device_mode,t` | `{lastUpdate, action, firstImageToDisplay, wakeUpSchedule:[a,b]}` |
| `dev/playlist/detail` | `device_id,t` | `{list:[{id,name,createDate,path:…<id>.bmp, thumbPath:…<id>.jpg}]}` |
| `GET /e_frame_image/<serial>/<id>.bmp` | — | the 4bpp panel BMP (**no `If-None-Match`/ETag**) |
| `callback/action_status` | `device_id,t,action_code` | `{"code":"success","message":"action code updated succefully."}` |
| `callback/power_off` | `device_id` | `{"code":"success","message":"power off status sync succefully."}` |

`action`: **2** = fetch + display a new image; **0** = idle (heartbeat). On wake the frame sends one
`dev/frame/status` (gets `action:2`), `dev/playlist/detail`, downloads the `.bmp`, posts
`callback/action_status`, then ~55× `dev/frame/status` (all `action:0`) while the e-paper renders,
then `callback/power_off`. `wakeUpSchedule = [a,b]` with **a+b = `wakeUpInterval` (259200 = 3 days)**;
the frame sleeps on the first value (~2 days observed).

**Confirmed by the wake:** it fetched **`1782501676382714867.bmp`** — the exact image we'd pushed —
so the byte-exact panel BMP and `path`/`thumbPath` shapes are right, it wants **`.bmp`** (not `.jpg`),
and it uses **no ETag**.

**Implemented (this is what the frame actually calls; the app endpoints in §4 stay for the app):**
- `dev/frame/status` — returns `action` 2→0 using a tiny per-frame state (`frame_display` table:
  `content_key`/`last_update_ms`/`acked_key`), so the frame displays once then the 55 heartbeats stay
  idle and it sleeps. `wakeUpSchedule = [172800, 86400]`.
- `dev/playlist/detail` — reuses the `_photo_items` builder (`path` .bmp + `thumbPath` .jpg).
- `callback/action_status` (marks the current image acked → `action` goes idle) and `callback/power_off`.
- Frames register by **`device_id`**; everything (cache, image path, curation) keys on that id.

Still open (not blocking cutover): persisting the frame's reported `battery`/`rssi`/`fw` telemetry to
the status UI, and the OTA/firmware path (#12).

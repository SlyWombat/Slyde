# SwitchBot AI Art Frame — initial investigation

Working notes for adding a `switchbot` backend to Slyde. Hardware ordered (not yet in hand); this
is the desk-research phase. Mirrors the playbook from `../aluratek-eframe/`.

## The product
**SwitchBot AI Art Frame** — a battery, e-paper "art"/photo frame (AI-art generation is the headline
marketing feature; for us it's just a photo frame fed from the SwitchBot app/cloud).

| | 7.3" | **13.3"** | 31.5" |
|---|---|---|---|
| Panel | 800×480 e-paper | **E-Ink Spectra-6, 1200×1600** | 2560×1440 |
| Notes | | **same panel family as the Aluratek/Sungale eFrame we already support** | larger |

- **Connectivity:** 2.4 GHz Wi-Fi + **BLE 4.2** (BLE is for pairing; Wi-Fi for content). Battery
  2000 mAh, quoted ~2 years → **very low duty cycle: it sleeps and polls infrequently**, like the
  eFrame. Stores ~**10 images locally**; displays cached images offline.
- **App:** the SwitchBot app uploads/schedules photos + AI art (Google "NanoBanana"/Gemini, paid).
- **Smart-home:** Alexa, HomeKit, Google Home, **Matter 1.3** (control only — Matter has no
  image/photo device type, so it won't help deliver photos).

## Why this is a strong candidate (big head starts)
1. **Same panel as the eFrame.** The 13.3" is **E-Ink Spectra-6, 1200×1600** — exactly what
   `panel_bmp.py` already encodes byte-exact for the Aluratek `EL133UF1`. The image pipeline is very
   likely reusable (verify the on-wire byte layout once we can capture one).
2. **Served-backend pattern reuse.** A battery e-paper frame that sleeps and polls a cloud is the
   same shape as `sungale-cloud` (`ServedFrameBackend`): we impersonate the content endpoint and it
   pulls on wake. The whole delivery-queue + identity + serving machinery already exists.
3. **An OFFICIAL control API exists** (unlike Memento/Sungale — no reverse-engineering needed for
   control).

## ✅ MAJOR UPDATE — the official API does EVERYTHING (control **and** delivery)
Reading the actual API doc (not just secondary sources) overturned the "can't upload images"
assumption: **the AI Art Frame's command set includes `uploadImage`** (verified verbatim in the
[README command table](https://github.com/OpenWonderLabs/SwitchBotAPI/blob/main/README.md)). So
there is **no reverse-engineering, no DNS redirect, no cloud impersonation** — Slyde drives the frame
entirely through SwitchBot's signed cloud API. This is the **cleanest of the three frames**.

**Auth:** token + secret, **HMAC-SHA256 signed headers** (`Authorization`, `sign`, `t` 13-digit ms,
`nonce` UUID; `sign = base64(HMAC-SHA256(secret, token+t+nonce)).upper()`). Base URL
`https://api.switch-bot.com`, rate limit **10 000 calls/user/day**. `deviceType` = **"AI Art Frame"**
(models `W8402000`/`W8402010`/`W8402020` = 7.3"/13.3"/31.5").

**Commands** — `POST /v1.1/devices/{id}/commands`, body `{"commandType":"command","command":…,"parameter":…}`:
| command | parameter | effect |
|---|---|---|
| `next` | `"default"` | next image |
| `previous` | `"default"` | previous image |
| `uploadImage` | `{"imageUrl":"https://…"}` **or** `{"imageBase64":"data:image/jpeg;base64,…"}` | **set the displayed photo** |

**Status** — `GET /v1.1/devices/{id}/status` body: `battery` (0-100), `displayMode` (0=static, 1=slideshow),
`imageUrl` (current image), `version` (firmware). Also a **webhook** (`changeReport`: displayMode,
battery, deviceMac) for push updates. `GET /v1.1/devices` lists devices (filter `deviceType`).

So **`panel_bmp.py` is probably NOT even needed** — `uploadImage` takes a JPEG (URL or base64) and
SwitchBot's cloud renders it to the Spectra-6 panel. Slyde just prepares a panel-fit JPEG (1200×1600)
and pushes it. (Confirm rendering quality against the device; if we want exact dither control, we
could pre-render — but the simple path should be fine.)

## Backend shape — a clean push-via-cloud-API backend
The `switchbot` backend doesn't fit "served" (the frame doesn't poll *us*) or classic "connected"
(no LAN session) — it's a **third transport: we push to the frame via the vendor's cloud API**. It
maps onto the delivery queue as a connected-style backend whose "deliver" = `uploadImage`, with
`capabilities = interaction=connected (push), color_model=epaper`. Discovery = `list_devices`.
Per-frame credentials (token/secret) configured per account.

## Prototype — DONE ✅
`packages/slyde-backend/src/slyde_backend/switchbot.py` — `SwitchBotClient` (signed requests;
`list_devices`/`art_frames`, `art_frame_status`, `next_image`/`previous_image`,
`upload_image_url`/`upload_image_bytes`). Unit-tested in `tests/test_switchbot.py` (mocked transport):
the HMAC signature vector, device filtering, status parse, the exact command payloads, and error
handling. Ready to run against a real token/device.

## ✅ VALIDATED LIVE (2026-06-29, against a real frame)
Ran `scripts/switchbot_smoke.py` with the existing SlyClaw SwitchBot Open API creds
(`SWITCHBOT_TOKEN`/`SWITCHBOT_SECRET`, reused — HA itself uses local BLE, not these). Results:
- **Auth / list / status all work.** The signed client reached the cloud; the account has 13 devices;
  the test unit "Kazoo Small Frame" (`B0E9FEDEF6E2`) reported `battery=100`, `mode=static`,
  `fw=V1.0-1.2`, and a `current` **AWS S3 presigned URL** (`art-frame-eu-prod`, eu-central-1).
- **`uploadImage` works end-to-end.** Pushed a JPEG → the API accepted it → after a wake the frame's
  `current` changed to a new S3 object (`main_<hash>.jpg`) and **the image displayed on the panel**.
- **Panel = the 7.3" model = ~3.5"×6" = 480×800 PORTRAIT, Spectra-6.** (Not landscape.)
- **SwitchBot's cloud center-CROPS to fill (cover), it does not letterbox.** A landscape image lost its
  sides; a portrait 3:5 image filled the frame edge-to-edge. → **Slyde must pre-fit to 480×800 portrait.**
- **Redraw latency ≈ 30 s** after the frame wakes (e-paper refresh + S3 pull). Delivery is therefore
  "queued, lands on next wake," not instant — the backend should report `delivered` optimistically /
  reconcile against `status.imageUrl`, like a served frame.

## Remaining — build the backend
1. Wire `SwitchBotClient` into a **`switchbot` FrameBackend** on the delivery queue: deliver =
   prep photo to **480×800 portrait** (Slyde's smart-fit, so the cloud's cover-crop is a no-op) →
   `upload_image_bytes`. Discovery = `list_devices`. Status/battery from `art_frame_status`.
2. Per-account creds via config (`SWITCHBOT_TOKEN`/`SWITCHBOT_SECRET`, already in `.env.example`).
3. Conformance test; add to `docs/supported-frames.md`; deploy creds into the kdocker2 stack env.

## Resolved/no-longer-relevant questions
- Cloud-poll vs LAN vs BLE → moot; we **push via the official API** (`uploadImage`).
- `panel_bmp.py` → **not needed**; send a JPEG, the cloud renders to Spectra-6.
- TLS-pinning / MITM → moot; no impersonation.
- Identity → the API addresses the frame by its `deviceId` (e.g. `B0E9FEDEF6E2`).

## Sources
- <https://us.switch-bot.com/products/switchbot-ai-art-frame>
- <https://github.com/OpenWonderLabs/SwitchBotAPI>
- <https://github.com/OpenWonderLabs/SwitchBotAPI/issues/461>
- Reviews: Tom's Guide, SmartHomeScene, Techlicious (specs + connectivity).

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

## The two planes
### Control plane — official SwitchBot OpenAPI ✅ (documented, no RE)
- Repo: <https://github.com/OpenWonderLabs/SwitchBotAPI>. Base URL `https://api.switch-bot.com`,
  auth = token + secret with **HMAC-SHA256 signed headers** (`Authorization`, `sign`, `t` 13-digit
  ms timestamp, `nonce` UUID; sign = base64(upper(HMAC-SHA256(secret, token+t+nonce)))). Rate limit
  **10 000 calls/user/day**. `deviceType` = **"AI Art Frame"**.
- Supports: **battery level**, and **next / previous image** control. Get device list + status via
  `GET /v1.1/devices` and `GET /v1.1/devices/{id}/status`.
- **Does NOT support uploading/removing images** (the gap). Tracked upstream:
  [OpenWonderLabs/SwitchBotAPI#461](https://github.com/OpenWonderLabs/SwitchBotAPI/issues/461)
  ("Add CRUD image functionality… in the API", filed Jan 2026) — not in our control.

### Content plane — how photos actually get on the frame ❓ (the unknown to RE)
The OpenAPI can't push images, so photo delivery has to come from how the **app** does it. Hypotheses
to confirm by capture (frame in hand):
- **(A) Cloud poll** — the frame wakes and fetches its playlist + images from a SwitchBot cloud host
  → **cloud impersonation** (`ServedFrameBackend`), redirect its host via DNS, reuse `panel_bmp.py`.
  *Most likely given the battery/e-paper/sleep profile — same as the eFrame.*
- **(B) Direct LAN upload** — the app pushes over Wi-Fi to the frame on the LAN → a connected-ish
  local protocol.
- **(C) BLE bulk transfer** — less likely for full images (BLE 4.2 is slow); probably pairing only.

## Investigation plan (when the frame arrives)
1. **Set up the official API** — create a SwitchBot token/secret, list devices, confirm `deviceType`,
   read status (battery), exercise next/previous. Prototype a tiny signed-request helper.
2. **MITM the app's photo upload** (the key step) — capture the SwitchBot app uploading a photo to
   the frame (OPNsense pcap + DNS logging, same as the eFrame `capture_wake.py` approach). Determine
   which hypothesis (A/B/C) holds and the exact endpoints/format.
3. **Capture a frame wake/poll** — confirm the host it contacts, cadence, identity (device id/serial),
   and whether content is pulled (A) vs pushed.
4. **Panel format** — pull/derive one on-wire image and diff against `panel_bmp.py`'s Spectra-6
   encoder; adapt if the byte layout differs.
5. Decide the backend shape: most likely **served (content) + OpenAPI (control)** hybrid behind one
   `switchbot` backend, reusing the delivery queue + panel codec.

## Open questions
- Cloud-poll vs LAN-upload vs BLE for image delivery? (drives connected-vs-served)
- Does the SwitchBot cloud TLS-pin? (affects MITM + DNS-redirect feasibility)
- Is the 13.3" panel byte-identical to `EL133UF1`, or just the same Spectra-6 family?
- Identity: how does the frame identify itself on its content requests (device id / serial / token)?

## Sources
- <https://us.switch-bot.com/products/switchbot-ai-art-frame>
- <https://github.com/OpenWonderLabs/SwitchBotAPI>
- <https://github.com/OpenWonderLabs/SwitchBotAPI/issues/461>
- Reviews: Tom's Guide, SmartHomeScene, Techlicious (specs + connectivity).

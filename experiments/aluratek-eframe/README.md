# Aluratek eFrame revival — recon toolkit

Interoperability tooling to keep an **Aluratek 13.3" ePaper WiFi frame you own**
(model `AEINK13F`, FCC ID `RDUAEINK13F`, app `com.xiaowooya.aluratek_eframe`)
working if Aluratek's cloud is discontinued — feeding it one-way, read-only from
your own Immich library, the same principle as Memento Manager.

> **This is recon, not a finished revival.** The frame is a sleep-most-of-the-time
> MCU (pre-certified WiFi module — almost certainly ESP32-class) that polls a
> cloud over HTTPS. We don't yet know its API; these tools are how we learn it.

## Device facts (verified)

| | |
|---|---|
| Model / FCC ID | `AEINK13F` / `RDUAEINK13F` (grantee RDU = Aluratek) |
| Display | 13.3" color e-paper (Spectra 6), holds ~10 images, shows 1 |
| Power | Battery, ~2-year life → deep-sleep MCU, **not Android** (no ADB) |
| Connectivity | 2.4 GHz WiFi, polls cloud ~every 3 days; phone app sends from anywhere |
| App | Aluratek **eFrame** — `com.xiaowooya.aluratek_eframe` (white-label ODM "xiaowooya") |
| Frame firmware | **v2.0.26** (versioned ⇒ has an update channel — see OTA note) |
| App settings surface | device-config + **scheduling** (orientation, photo-switching, on/off schedule) — the cloud API is more than image-push |
| FCC teardown | Internal photos **embargoed** (new device; ~180-day confidentiality) |

The app exposes a **Frame ID** (cloud routing key) + **Serial Number** per device —
expect both in the captured pairing/auth calls. (Keep real values out of this repo.)

## The plan (three parallel tracks)

1. **Phone-side MITM** (`mitm_eframe.md`) — no waiting. Decrypt the app↔cloud API.
2. **APK static analysis** (`apk_extract.md`) — no waiting. Read endpoints + the
   cert-pinning config straight out of the app.
3. **Frame-side capture** (OPNsense + AdGuard Home) — gated on the 3-day wake.
   Force the frame's DNS through AGH (OPNsense NAT :53 → AGH), watch the AGH
   **Query Log** for the cloud hostname, and run a `tcpdump` ring buffer to grab
   the **TLS SNI** (the one thing AGH can't show) to judge pinning.

All three converge on the same answer: **the cloud hostname + API + whether the
frame validates TLS.**

## OTA note — a possible *no-hardware* reflash path

The frame reports a versioned firmware (`2.0.26`), so it checks the cloud for
updates. If we control the cloud (AGH DNS-rewrite) **and** the frame doesn't
verify firmware signatures, we may be able to push **custom firmware OTA without
opening the device**. So the capture/APK has THREE targets, not one:
1. the **image-fetch** endpoint (serve Immich),
2. the **config/schedule** endpoint (keep settings working),
3. the **firmware/OTA** endpoint + whether updates are **signed**.

## Then: stand in for the cloud

`fake_cloud.py` — a FastAPI catch-all that first **logs** everything the frame
asks (RECON), then **serves Immich images** for the identified image path
(REPLACE). Point the frame at it with an **AdGuard Home DNS Rewrite** of the
captured hostname.

```bash
# from the repo root (uses the workspace's httpx/fastapi)
export IMMICH_BASE_URL=http://<immich>:2283 IMMICH_API_KEY=<key>
export IMMICH_ALBUM_ID=<optional-album-uuid>
# self-signed cert (works ONLY if the frame doesn't validate — recon proves it):
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 825 \
  -subj "/CN=<captured-cloud-hostname>"
uv run uvicorn fake_cloud:app --host 0.0.0.0 --port 443 \
  --ssl-keyfile key.pem --ssl-certfile cert.pem
```

## Go / no-go decision

- **Frame doesn't validate TLS** (common on cheap IoT) → DNS-rewrite + `fake_cloud`
  wins. Build out the REPLACE stage and we're done.
- **Frame pins / validates against a fixed CA** → MITM impossible without touching
  the device → firmware-reflash route (dump via UART/SWD; if it's an unencrypted
  ESP32, `esptool.py` + custom firmware that fetches from Memento Manager).

The recon tells us which — don't build the replacement until capture confirms
no validation.

# Slyde — User Guide

Slyde brings a discontinued **Memento Smart Frame** back to life: it sends photos
from your **Immich** library to the frame over your local network — no cloud, no original app.

## What you need
- A Memento Smart Frame powered on and connected to your Wi-Fi/LAN.
- An [Immich](https://immich.app) server with your photos, and an Immich **API key**
  (Immich → *Account Settings → API Keys*).
- A machine on the **same LAN** as the frame to run Slyde (Docker recommended).

## Quick start (Docker)
```bash
git clone https://github.com/SlyWombat/slyde.git
cd slyde
cp .env.example .env          # then edit .env (see below)
docker compose up -d
```
Open **http://<that-machine>:8080/**.

### Minimal `.env`
```ini
# Leave FRAME_HOST empty to auto-discover the frame; or set its IP if discovery can't reach it.
FRAME_HOST=
IMMICH_BASE_URL=http://your-immich-host:2283
IMMICH_API_KEY=paste-your-key-here
```
That's it — `FRAME_CANVAS` defaults to the 35″ frame's 3240×2160; change it only for other models.

> **Finding the frame's IP:** check your router's client list, or run
> `docker run --rm --network host slyde:latest memento discover`.
> Broadcast discovery needs the app on the same subnet as the frame; if they're separated
> (VLANs, or Docker bridge networking), set `FRAME_HOST` to the frame's IP.

## Using the app
1. **Pick a frame.** The start screen lists frames found on your network — click one to manage it
   (or **Rescan**). If discovery can't reach it, set `FRAME_HOST` and it appears automatically.
2. **See what's on the frame.** The frame's own **albums** are shown (including the built-in
   *Photos* album holding everything); click an album to view its **existing thumbnails**.
   **Create** a new album with the box at the top right. Hover a thumbnail and click ✕ to remove it.
3. **Add photos to an album.** Select the target album, then in **Add photos**:
   - **From Immich** — choose an Immich album, then *Add whole album* or tick photos and *Add selected*.
   - **Upload files** — pick image files from your computer to send directly.
   Each image is auto-resized/letterboxed to the frame and added to the chosen album (or *Photos*).
4. **Control & settings** (right): name/firmware, **Previous/Next**, and toggles for display on/off,
   shuffle, night mode, portrait, and slide duration.

Re-running an Immich sync is safe — unchanged photos are skipped (tracked by content hash).

## Running without Docker (development)
```bash
uv sync
uv run slyde-backend          # API + UI on http://localhost:8080
# in another terminal, for UI hot-reload:
cd frontend && npm install && npm run dev   # http://localhost:5173
```

## Try it without a real frame
A faithful frame **emulator** ships with the project, with a **visual web UI** that shows what the
frame would display (current image, albums, photos) and updates live as you upload.
```bash
uv run memento-emulator --name "Test Frame"     # web UI on http://localhost:8099
# point the backend at it:  FRAME_HOST=127.0.0.1
```
Or run it as its own container:
```bash
docker compose -f deploy/emulator/compose.yaml up --build   # web UI on :8099
```

## Privacy & security
- Everything stays on your LAN. The app never contacts any Memento cloud service (there isn't one).
- The frame exposes its Wi-Fi password on the LAN (a firmware quirk); Slyde never logs,
  stores, or displays it.
- Keep your Immich API key in `.env` (gitignored) — never commit it.

## Troubleshooting
| Symptom | Fix |
|--------|------|
| "Frame unavailable" | Confirm the frame is on and on the same subnet; set `FRAME_HOST` to its IP. |
| "Immich not configured" badge | Set `IMMICH_BASE_URL` and `IMMICH_API_KEY` in `.env`, restart. |
| Thumbnails don't load | Check the Immich URL is reachable from the container and the key is valid. |
| Discovery finds nothing | Broadcast can't cross Docker bridge / VLANs — set `FRAME_HOST`. |

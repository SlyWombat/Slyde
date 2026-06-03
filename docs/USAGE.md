# Memento Manager — User Guide

Memento Manager brings a discontinued **Memento Smart Frame** back to life: it sends photos
from your **Immich** library to the frame over your local network — no cloud, no original app.

## What you need
- A Memento Smart Frame powered on and connected to your Wi-Fi/LAN.
- An [Immich](https://immich.app) server with your photos, and an Immich **API key**
  (Immich → *Account Settings → API Keys*).
- A machine on the **same LAN** as the frame to run Memento Manager (Docker recommended).

## Quick start (Docker)
```bash
git clone https://github.com/SlyWombat/memento-manager.git
cd memento-manager
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
> `docker run --rm --network host memento-manager:latest memento discover`.
> Broadcast discovery needs the app on the same subnet as the frame; if they're separated
> (VLANs, or Docker bridge networking), set `FRAME_HOST` to the frame's IP.

## Using the app
- **Frame panel** (top right): the frame's name, firmware, screen, and **Previous / Next** buttons.
- **Settings**: toggle display on/off, shuffle, night mode, portrait, and the slide duration.
- **Immich albums** (left): pick an album, then either **Sync whole album** or tick individual
  photos and **Sync selected**. Each photo is resized/letterboxed to the frame and uploaded.
- **On the frame**: everything you've synced; **Remove** deletes a photo from the frame.

Re-running a sync is safe — unchanged photos are skipped (tracked by content hash).

## Running without Docker (development)
```bash
uv sync
uv run memento-backend          # API + UI on http://localhost:8080
# in another terminal, for UI hot-reload:
cd frontend && npm install && npm run dev   # http://localhost:5173
```

## Try it without a real frame
A faithful frame **emulator** ships with the project, so you can explore safely:
```bash
uv run memento-emulator --host 127.0.0.1 --name "Test Frame"
# then point the backend at it: FRAME_HOST=127.0.0.1
```

## Privacy & security
- Everything stays on your LAN. The app never contacts any Memento cloud service (there isn't one).
- The frame exposes its Wi-Fi password on the LAN (a firmware quirk); Memento Manager never logs,
  stores, or displays it.
- Keep your Immich API key in `.env` (gitignored) — never commit it.

## Troubleshooting
| Symptom | Fix |
|--------|------|
| "Frame unavailable" | Confirm the frame is on and on the same subnet; set `FRAME_HOST` to its IP. |
| "Immich not configured" badge | Set `IMMICH_BASE_URL` and `IMMICH_API_KEY` in `.env`, restart. |
| Thumbnails don't load | Check the Immich URL is reachable from the container and the key is valid. |
| Discovery finds nothing | Broadcast can't cross Docker bridge / VLANs — set `FRAME_HOST`. |

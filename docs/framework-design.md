# Framework design — the multi-frame hub

This document designs the framework that lets Slyde grow from "one LAN frame" into the
**central frame hub** of ADR-010 — cleanly, without regressing Memento (ADR-009). It is the
groundwork the open issues build on. It is a *design*, decomposed into implementation issues below;
nothing here changes Memento's behavior.

## 1. The gap

Today the architecture is **host-keyed and connection-oriented**:

- A frame is identified by a LAN `host` string.
- `FrameService._with_client(host, fn)` opens a `FrameBackend.session(host)` and the manager
  **pushes** images / reads state. (`FrameConnection` Protocol — the manager initiates.)
- `SyncService` prepares images for the frame's canvas and pushes them immediately; curation state
  lives in `Store`, keyed by `(host, asset_id)`.

That fits Memento perfectly. It does **not** fit the next class of frame. The Aluratek/Sungale
e-ink frame is **inverted**:

- The manager **never connects to the frame**. The frame sleeps, wakes every ~3 days, and **polls a
  cloud** over HTTP. We revive it by *impersonating that cloud* (DNS rewrite → our server).
- There is no `host` to `session()` into. The frame's identity is a **frame-code** it presents.
- Delivery is **pull, not push**: the manager must answer the frame's polls with prepared images,
  whenever the frame happens to wake.

So the framework needs two **interaction models**, a frame **identity** that isn't a LAN host, and a
**curation/delivery split**.

## 2. Target architecture

```
                         ┌──────────────────── Slyde (the hub) ────────────────────┐
   Immich (read-only) ─► │  ImageSource ─► FrameLibrary (desired set / frame) ─► Processing   │
                         │                      │                profile (/frame)             │
                         │                      ▼                                              │
                         │   ┌── ConnectedBackend (LAN) ──► session() ──► push ──► Memento ───┼─► frame
                         │   └── ServedBackend (cloud) ──► router() ◄── poll ◄── pull ─────────┼─◄ frame
                         │   FrameRegistry · Scheduler · Health · OTA  (frame-agnostic plane)   │
                         └────────────────────────────────────────────────────────────────────┘
```

Five abstractions, four new:

### 2.1 Frame identity & registry  *(new)*
Replace bare `host: str` with a **`Frame`** value: `id`, `backend`, `address` (connected) **or**
`frame_code`/identity (served), `capabilities`, `profile`, `last_seen`. A persistent **registry**
(extends `Store`) tracks known frames across backends. Existing host-keyed APIs keep working — a
Memento frame's `id` is its host — so there is no Memento regression.

### 2.2 Backend interaction models  *(new — the crux)*
`FrameBackend` gains an `interaction` kind and splits into two sub-interfaces:

- **`ConnectedFrameBackend`** — today's `discover()` + `session(host) -> FrameConnection`. The
  manager initiates and pushes/reads. (Memento LAN; unchanged.)
- **`ServedFrameBackend`** — the manager runs a server the frame polls. Provides:
  - `router() -> APIRouter` — the HTTP surface the frame calls (mounted into the app).
  - `identify(request) -> frame_id` — which registered frame is calling (frame-code/token).
  - `respond(frame, request)` — answer a poll, pulling from `FrameLibrary` + the frame's
    `ProcessingProfile`. No connection to the frame is ever made.

`FrameCapabilities` already exists; add `interaction: "connected" | "served"`.

### 2.3 Per-frame processing profile  *(issue #19)*
Image preparation is a property **of the frame**, not a global setting. A `ProcessingProfile`
(resolution, fit, **color model** — full-color vs e-ink palette — **dither**, output encoding) is
owned by the backend/frame; `prepare_for_frame` routes through it. Memento LCD profile = today's
canvas+`FRAME_FIT` (byte-for-byte unchanged); Sungale e-ink profile adds Spectra-6 palette + dither.

### 2.4 Curation/delivery split — `FrameLibrary`  *(new)*
Generalize `Store` + `SyncService` so that **what a frame should show** (curation, sourced
read-only from Immich) is decoupled from **how it gets there** (delivery):

- **Connected** backends *reconcile by push* (today's sync/mirror) when the manager runs.
- **Served** backends *expose the set for pull*; the frame fetches on wake; the manager serves the
  already-**prepared** image from the cache (§2.6).

One curation model, two delivery strategies — chosen by the backend's interaction kind.

### 2.6 Sync is a backend service; the hub caches prepared images; the UI is read-only  *(RULE, #25)*
All sync activity (keeping an Immich album in sync with a frame folder) runs as an **autonomous
backend service** — the scheduler reconciles subscriptions on its own; one-off "sync now" is just a
backend job. **The UI never drives or blocks on sync**: it is a disconnected, read-only view of
current state (last run, what's cached/queued, what's on the frame) and can be closed at any time
without affecting anything.

The hub keeps **copies of the prepared (edited) images** — smart-blur edges, fit, e-ink
palette/dither already applied — in a per-frame **`ImageCache`**, *ready to send*. This decouples
*processing* from *delivery*, which is essential for served frames that wake on their own schedule
(they must be handed an already-prepared image — `CachedImageDelivery` reads from this cache), and
lets connected frames avoid re-processing on every sync. The cache is filled by the sync/processing
service and refreshed when curation or a frame's processing profile changes.

### 2.5 Frame-agnostic plane: scheduling · health · OTA  *(generalize)*
Scheduler, `/health/sync`, and the OTA/firmware service become frame-agnostic (keyed by `Frame`,
not `host`), so heterogeneous frames are scheduled, monitored, and updated from one plane.

## 3. Backward-compatibility contract (ADR-009)
- Memento stays a **connected, host-keyed** frame; its code path is unchanged and remains the
  default backend. The emulator + integration + `memento-lan` conformance tests are the regression
  guard and must stay green at every step.
- New abstractions are introduced **additively** (a Memento frame is just a `Frame` whose `id` is
  its host and whose interaction is `connected`).

## 4. Build order (implementation issues)
The framework is decomposed so each piece lands independently, Memento-green throughout:

1. **Frame identity & registry** — the `Frame` value + persistent multi-backend registry.
2. **Backend interaction models** — split `FrameBackend` into connected/served; add `interaction`.
3. **Served-backend HTTP mounting & identification** — backends contribute routers; identify the
   caller; wire registry → app.
4. **Curation/delivery split (`FrameLibrary`)** — decouple desired-set from push/pull delivery.
5. **Per-frame processing profile** — issue #19.
6. **Frame-agnostic scheduling / health / OTA plane.**

The Aluratek/Sungale eFrame work (#8 responder, #14 backend, #11 e-ink profile) is the **first
consumer** of (2)–(5): it implements a `ServedFrameBackend` whose `respond()` serves Immich images
through an e-ink `ProcessingProfile`. Memento is the reference **connected** backend.

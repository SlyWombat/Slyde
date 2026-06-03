# Memento Manager — Architecture

A self-hosted, **generic** service to manage the photos shown on a Memento Smart Frame, sourcing
images from an **Immich** library, with a modern web UI. Containerized; runs on any Docker host.

> Status: DESIGN (for review). Implementation proceeds in reviewed increments. See `PLAN.md`
> for phase tracking and `protocol.md` for the reverse-engineered device protocol.

> **Design principle — reusable, nothing hardcoded.** This is built for *any* Memento frame
> owner and intended to be published openly. No environment-specific value (frame IP, Immich
> URL/key, hostnames, host paths, deployment topology) appears in shipped code — everything is
> configuration with sensible defaults (12-factor). Our own kdocker/Immich setup appears in this
> doc and in `deploy/` only as **one example deployment**, clearly labelled, never baked in.

## 1. Goals & non-goals
**Goals**
- Browse the Immich library and curate which photos appear on the frame.
- Push/remove photos and organize them into folders on the frame; adjust the display settings the
  protocol exposes today (on/off, shuffle, slide duration, night mode, orientation/portrait,
  rename) using the recovered local protocol. (Per-image reorder and the panel-calibration
  settings — brightness, contrast, away schedule — are recognized by the protocol layer but not
  yet surfaced in the UI.)
- Reflect live frame state (current image, config, albums, thumbnails).
- Run as a lightweight container on kdocker (ARM64, tight RAM), managed as a Dockge stack.
- Fully testable without the physical frame, via a faithful **frame emulator**.

**Non-goals (initially)**
- No cloud dependency (the original service is dead; we are LAN-only).
- No re-flashing/firmware mods of the frame. We speak its existing protocol.
- No multi-frame fleet management in v1 (design keeps it possible: frames are addressable by IP/GUID).

## 2. Components (monorepo)
```
memento/
├── packages/
│   ├── memento-core/        # Python: protocol client lib (crypto, discovery, control, file)
│   ├── memento-emulator/    # Python: faithful frame server for tests & local dev
│   └── memento-backend/     # Python/FastAPI: REST API, Immich integration, orchestration
├── frontend/                # React + TS + Vite + Tailwind SPA
├── deploy/                  # Dockerfiles, compose stack for kdocker (Dockge), CI
├── docs/                    # protocol.md, architecture.md, ADRs
└── tests/                   # cross-package integration tests (client <-> emulator)
```
- **memento-core** — pure library, no web deps. Owns the protocol (see `protocol.md`).
  Sync API now; an async variant can wrap it later if needed.
- **memento-emulator** — implements the frame's *server* side of the protocol so the backend
  and core are tested end-to-end (incl. uploads) with zero production risk. Also a dev "virtual
  frame" the real Windows app could even talk to.
- **memento-backend** — FastAPI. Talks to Immich (REST) and to the frame (via memento-core).
  Persists curation/sync state (SQLite). Serves the built SPA as static files (single container).
- **frontend** — SPA. Talks only to the backend REST API.

## 3. Runtime topology (generic)
```
                 Any Docker host on the frame's LAN
  ┌──────────────────────────────────────────────────────┐
  │  memento-backend container (FastAPI + static SPA)      │
  │     ├── Immich REST  ──► $IMMICH_BASE_URL (configured) │
  │     └── memento-core ──► (LAN) ──► frame (discovered    │
  │                                    or $FRAME_HOST)      │
  └──────────────────────────────────────────────────────┘
```
- The backend reaches the frame on the LAN by **discovery** (UDP broadcast) or an explicit
  `FRAME_HOST`/IP from config. Both supported; neither hardcoded.
- Immich is any reachable instance via `IMMICH_BASE_URL` + `IMMICH_API_KEY`.
- A single small container (API serves the SPA) suits low-memory hosts; nothing assumes a
  particular host, network, or path.

### Example deployment (ours — illustrative only, see `deploy/examples/kdocker/`)
Host **kdocker** (NanoPi M5, ARM64, Dockge stacks at `/data/stacks/<name>/compose.yaml`),
Immich already at `http://immich:2283`, frame on the same LAN. Memory is tight (~1 GB free), so
the single-container design matters here — but none of this is assumed by the code; it's one
filled-in instance of the generic config below.

## 4. Architecture Decision Records (ADRs)
- **ADR-001 Backend = Python/FastAPI.** Reuse the already-validated Python protocol library
  directly; one language for all device code; strong async + test story. (Rejected: Node/TS —
  would require re-implementing the protocol/crypto.)
- **ADR-002 Frontend = React + TS + Vite + Tailwind** (+ shadcn/ui, TanStack Query). Mainstream,
  rich ecosystem, fast modern UI.
- **ADR-003 Ship standard Docker images + a generic Compose file.** The `Dockerfile` and
  `Dockerfile.emulator` build on the host architecture (we build them directly on the ARM64
  kdocker target); a portable `compose.yaml` and an `.env.example` are provided. Automated
  multi-arch (amd64 + arm64) publishing via buildx is a planned addition, not yet wired in CI.
  Orchestrator-specific glue (e.g. our Dockge stack, or a k8s manifest) lives under
  `deploy/examples/` only — the image and compose file assume no particular orchestrator.
- **ADR-004 Single container** (API serves SPA) rather than separate nginx — minimizes the
  memory footprint on the 4 GB NanoPi. A reverse proxy is unnecessary on the LAN; external
  exposure (if ever) goes through the existing Cloudflare Tunnel like other services.
- **ADR-005 Persistence = SQLite** (file on a bind-mounted volume). Curation state, sync log,
  frame registry. No separate DB container (memory budget). Migratable to Postgres later.
- **ADR-006 Immich access = REST API + API key**, supplied entirely by config
  (`IMMICH_BASE_URL`, `IMMICH_API_KEY`). No instance/URL/key is embedded. (Our example sources
  the key from kdocker's `/data/slyclaw/immich_auto_album/.env`, but that's deployment glue, not code.)
- **ADR-007 Emulator is a first-class, supported artifact** (not a test fixture afterthought):
  it has its own package, CLI, and compose service, and is the default target in dev/CI.
- **ADR-008 12-factor configuration, nothing hardcoded.** All runtime values come from env
  (Pydantic `BaseSettings`) with documented defaults; config is validated at startup. Frame
  target, Immich, DB path, bind address/port, canvas size, sync interval — all configurable.
  Shipped code contains no environment-specific constants. An `.env.example` documents every key.

## 5. Image pipeline (Immich → frame)
The frame canvas is **3240×2160 landscape** (portrait variant exists). Immich originals must be
transformed before upload:
1. Fetch original/derivative from Immich (`/api/assets/{id}/original` or thumbnail).
2. Fit to 3240×2160 per `FRAME_FIT` — `contain` (solid bars), `cover` (crop), `blur` (blurred
   sides), or `smart` (crop near-aspect, blur-fill portraits, the default).
3. Apply EXIF orientation; output JPEG (frame stores `.jpg`).
4. Upload via `WriteFile` (control 2017 + raw bytes 2018), then frame generates its thumbnail.
Transformations use Pillow. Frame upload is idempotent by destination filename; we track a
content hash to avoid re-uploading unchanged assets.

## 6. Testing strategy
- **Unit** (pytest): crypto round-trips & known-answer tests; framing/parse; image pipeline.
- **Integration** (pytest): backend + memento-core against **memento-emulator** in-process —
  discover, connect, get config, **upload**, list albums, change settings. No real frame.
- **Contract**: emulator validated against captured real-frame responses (golden fixtures from
  the live 6.02 frame) so it stays faithful.
- **Frontend**: `tsc --noEmit` typecheck, ESLint, and a production `vite build` (also run during
  the Docker image build). Component/e2e suites (vitest/Playwright) are a planned addition.
- **CI** (GitHub Actions): ruff (lint + format) + mypy (strict) + pytest with coverage. Frontend
  build and image publishing are not yet in CI (built locally / in the Docker image stage).

## 7. Quality gates
- Python: `ruff` (lint+format), `mypy --strict` on core/backend, `pytest` w/ coverage.
- TS: ESLint, `tsc --noEmit`, `vite build`.
- `pre-commit` hooks; Conventional Commits; PR-based review.

## 8. Security notes
- The frame returns Wi-Fi SSID/password in cleartext over the LAN (firmware weakness). The
  backend must never log or surface these. Treat all frame config as sensitive.
- Secrets (Immich API key, frame GUIDs) via env/secret files, never committed. `.env` gitignored.
- Backend binds to the LAN; any external exposure is the operator's choice (reverse proxy /
  tunnel), out of scope for the app itself.

## 9. Configuration (the only place deployment values live)
All via env (Pydantic `BaseSettings`), documented in `.env.example`. Representative keys:

| Key | Default | Purpose |
|-----|---------|---------|
| `FRAME_HOST` | _(empty → discover)_ | Explicit frame IP/host; empty enables UDP discovery |
| `FRAME_HOSTS` | _(empty)_ | Comma-separated extra hosts to always list in the picker (e.g. an emulator) |
| `FRAME_DISCOVERY` | `true` | Enable UDP broadcast discovery when no host is set |
| `FRAME_CANVAS` | `3240x2160` | Target image size `WxH` (portrait variant supported) |
| `FRAME_FIT` | `smart` | Off-aspect fit: `contain`\|`cover`\|`blur`\|`smart` (crop near, blur-fill far) |
| `FRAME_CROP_TOLERANCE` | `0.12` | Smart mode: crop if ≤ this fraction of the long edge is lost |
| `IMMICH_BASE_URL` | _(required)_ | Immich instance base URL |
| `IMMICH_API_KEY` | _(required, secret)_ | Immich API key |
| `IMMICH_ASSET_SIZE` | `preview` | Source fetched from Immich (`thumbnail`/`preview` or `original`) |
| `SYNC_INTERVAL_MINUTES` | `15` | How often kept-in-sync albums re-mirror (`0` disables the scheduler) |
| `DATABASE_URL` | `sqlite:///./memento.db` | State store (`/data/memento.db` in the image) |
| `BIND_HOST` / `BIND_PORT` | `0.0.0.0` / `8080` | API + SPA bind |
| `STATIC_DIR` | _(empty)_ | Built SPA directory to serve (set in the image) |
| `LOG_LEVEL` | `INFO` | Logging |

Secrets are never logged. Frame Wi-Fi credentials returned by the device are never persisted or
exposed by the API.

## 10. Open questions
- Curation model: mirror an Immich album → frame, or hand-pick a "frame playlist"? (Leaning:
  pick one or more Immich albums to sync, plus manual add/remove.)
- Do we manage multiple frames now or later? (Design allows later.)
- Sync trigger: on-demand from UI, scheduled, or Immich webhook? (Start: on-demand + scheduled.)

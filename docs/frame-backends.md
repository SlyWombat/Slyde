# Frame backends — adding a new frame

Slyde drives frames through a **`FrameBackend`** abstraction, so it isn't tied to one
device or one transport. The manager, web UI, and sync engine only depend on the interface — they
treat every backend (and the emulator) the same way.

This is what makes the project a *frame-revival toolkit* rather than a single-frame tool: each
abandoned or cloud-locked frame can be added as a backend without forking.

## Standing rules (ADR-009)

1. **The Memento LAN frame is always supported.** It is the founding, first-class target; adding
   any other frame must never regress it. CI guards this via the emulator + integration suite and
   the `memento-lan` conformance test — keep them green.
2. **Add frames behind this abstraction, never by special-casing the core.** The manager, web UI,
   and sync engine stay transport-agnostic.
3. **Make each frame's needs explicit — capabilities *and* processing.** A frame declares what it
   can do (`FrameCapabilities`) *and* how images must be prepared for its panel (its processing
   profile). The Memento LCD needs resolution/aspect fitting; the e-ink (Sungale Spectra-6) frame
   needs that plus palette mapping + dithering. Processing is a property of the frame, looked up per
   target — never assumed global.
4. **North star:** these backends compose into one **central "frame hub"** between Immich
   (read-only) and every frame (ADR-010). Build toward that, not toward per-frame forks.

## The interface

A frame is reached one of two ways, captured by two interaction models
(`packages/slyde-backend/src/slyde_backend/backends/base.py`):

- **Connected** (`ConnectedFrameBackend`) — *we* initiate sessions to the frame (LAN). Implements
  `discover()` and `session(host) -> contextmanager[FrameConnection]`, where **`FrameConnection`** is
  a `Protocol` of per-session operations: `get_config` / `change_setup`, `next_image` /
  `previous_image`, `get_current_image_name`, `upload_image` / `delete_image`, `get_album_data` /
  `send_album_data`, `get_thumbnails_list` / `get_thumbnail`, `trigger_update`.
  `memento_core.FrameClient` satisfies it structurally. *(memento-lan)*
- **Served** (`ServedFrameBackend`) — the *frame* polls a server we run (cloud devices we can't reach
  or connect to). Implements `router()` (the HTTP surface the frame polls), `identify(request)` (which
  frame is calling), and `respond(frame, request)`. We impersonate the vendor cloud and hand the
  frame an already-prepared image from the cache. *(sungale-cloud)*

Both declare a **`FrameCapabilities`** descriptor (interaction, transport, `color_model`, discovery,
albums, thumbnails, upload, delete, ota). `color_model` drives the per-frame processing profile:
`full` (LCD → JPEG) or `epaper` (Spectra-6 palette + dither → the panel's 4bpp BMP, see
`panel_bmp.py`).

## Selecting a backend

By config, never hardcoded: the `FRAME_BACKEND` env var (`Settings.frame_backend`) chooses the
registered backend. `FrameService` resolves it once via `get_backend(name)`.

```
FRAME_BACKEND=memento-lan     # default — the reverse-engineered Memento LAN protocol
FRAME_BACKEND=sungale-cloud   # Aluratek/Sungale Spectra-6 ePaper (cloud impersonation)
```

**One hub, multiple frames (ADR-010).** A served backend can be mounted *alongside* the primary via
`FRAME_SERVED_BACKENDS` (comma-separated), so a single Slyde drives both — e.g.
`FRAME_BACKEND=memento-lan` + `FRAME_SERVED_BACKENDS=sungale-cloud` runs the Memento frame and the
eFrame on one hub and one registry/delivery queue. This works because identity, delivery, and
processing are keyed per `frame.backend`, not globally. (Run exactly one hub against a set of frames.)

For a **served** backend the frame finds us by DNS: point an AdGuard Home rewrite of the vendor host
(`us.xiaowooya.eframe.sungale.com.cn`) at Slyde. The frame then polls Slyde's mounted endpoints and
downloads its image. App photo pushes (`photo/upload`) are ingested too — see
`docs/sungale-eframe-integration-plan.md`.

## Built-in backends

| Backend | Transport | Status | Notes |
|---|---|---|---|
| `memento-lan` | LAN (UDP+TCP) | ✅ complete | The original Memento frame, emulator, and Pi soft-frame. |
| `sungale-cloud` | Cloud (HTTP) | ✅ complete | Aluratek/Sungale Spectra-6 ePaper. Wire-confirmed cloud-impersonation responder (frame/list, album/detail, photo/upload, `/e_frame_image/<serial>/<id>.{bmp,jpg}` with ETags). Delivers the panel's exact byte-compatible 4bpp BMP (`panel_bmp.py`). Live verification = AGH cutover + the frame's ~3-day wake (#9). |

## Adding your own

1. Create `backends/<your_frame>.py` with a `class YourBackend(...)` that sets `name` +
   `capabilities`. Pick the interaction model:
   - **Connected** (`ConnectedFrameBackend`): implement `discover()` and `session()`, returning an
     object with the `FrameConnection` methods your frame supports (raise `NotImplementedError` for
     ones it can't, and reflect that in `capabilities`).
   - **Served** (`ServedFrameBackend`): implement `router()`, `identify()`, and `respond()` — the
     frame polls those endpoints (see `sungale_cloud.py`).
2. Register it in `backends/__init__.py` (`_BACKENDS`).
3. Add a conformance test (see `tests/test_backends.py` — run your backend against a fake/emulated
   device the way `test_memento_lan_backend_drives_the_emulator` does).
4. Document it in the table above and in `.env.example`.

For a real-world example of a non-LAN frame (a cloud device reached by impersonating its vendor
cloud), see the Aluratek/Sungale work in `experiments/aluratek-eframe/` and the `sungale-cloud`
backend.

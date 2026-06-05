# Frame backends — adding a new frame

Memento Manager drives frames through a **`FrameBackend`** abstraction, so it isn't tied to one
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

Two pieces (`packages/memento-backend/src/memento_backend/backends/base.py`):

- **`FrameConnection`** — a `Protocol` of the per-session operations the manager performs on a
  connected frame: `get_config` / `change_setup`, `next_image` / `previous_image`,
  `get_current_image_name`, `upload_image` / `delete_image`, `get_album_data` / `send_album_data`,
  `get_thumbnails_list` / `get_thumbnail`, `trigger_update`. `memento_core.FrameClient` satisfies it
  structurally; a new backend supplies its own object with the same methods.
- **`FrameBackend`** — an `ABC` with `discover()`, `session(host) -> contextmanager[FrameConnection]`,
  and a `FrameCapabilities` descriptor (transport, discovery, albums, thumbnails, upload, delete, ota).

## Selecting a backend

By config, never hardcoded: the `FRAME_BACKEND` env var (`Settings.frame_backend`) chooses the
registered backend. `FrameService` resolves it once via `get_backend(name)`.

```
FRAME_BACKEND=memento-lan     # default — the reverse-engineered Memento LAN protocol
FRAME_BACKEND=sungale-cloud   # Aluratek/Sungale ePaper (cloud impersonation — WIP)
```

## Built-in backends

| Backend | Transport | Status | Notes |
|---|---|---|---|
| `memento-lan` | LAN (UDP+TCP) | ✅ complete | The original Memento frame, emulator, and Pi soft-frame. |
| `sungale-cloud` | Cloud (HTTP) | 🟡 WIP | Aluratek/Sungale ePaper. Declared + registered; session impl pending (see `experiments/aluratek-eframe/`). |

## Adding your own

1. Create `backends/<your_frame>.py` with a `class YourBackend(FrameBackend)` that sets `name` +
   `capabilities` and implements `discover()` and `session()`. Return an object that implements the
   `FrameConnection` methods your frame supports (raise `NotImplementedError` for ones it can't do,
   and reflect that in `capabilities`).
2. Register it in `backends/__init__.py` (`_BACKENDS`).
3. Add a conformance test (see `tests/test_backends.py` — run your backend against a fake/emulated
   device the way `test_memento_lan_backend_drives_the_emulator` does).
4. Document it in the table above and in `.env.example`.

For a real-world example of a non-LAN frame (a cloud device reached by impersonating its vendor
cloud), see the Aluratek/Sungale work in `experiments/aluratek-eframe/` and the `sungale-cloud`
backend.

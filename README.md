# Memento Manager

Open tooling to revive the **Memento Smart Frame** after its cloud service was discontinued.
LAN-only, no cloud, nothing hardcoded to any particular deployment — built so any frame owner
can run it. Photos are sourced from an [Immich](https://immich.app) library and managed through
a modern web UI.

> The frame's local protocol was reverse-engineered from the discontinued official app. As far as
> we can find, this is the first public implementation. See [`docs/protocol.md`](docs/protocol.md).

## Repository layout
```
packages/memento-core/      Python library: the frame protocol (crypto, discovery, control, file)
packages/memento-emulator/  A faithful frame emulator — the default test/dev target
packages/memento-backend/   FastAPI service: Immich integration + frame orchestration   (planned)
frontend/                   React + TS + Vite + Tailwind web UI                          (planned)
deploy/                     Docker images + portable compose; example deployments
docs/                       protocol.md, architecture.md
tests/                      client <-> emulator integration tests
```

## Status
- ✅ Protocol reverse-engineered and documented; validated live against a firmware-6.02 frame.
- ✅ `memento-core` client library (discovery, config, display control, **image upload**).
- ✅ `memento-emulator` — full server-side emulator; client is tested end-to-end against it.
- ✅ `memento-backend` — FastAPI + Immich integration + image pipeline + sync state.
- ✅ Web UI (React/TS/Vite/Tailwind).
- ✅ Containerized; deployable via Docker Compose (see [`docs/USAGE.md`](docs/USAGE.md) and
  [`deploy/`](deploy/)).

See [`docs/USAGE.md`](docs/USAGE.md) for setup and [`docs/architecture.md`](docs/architecture.md)
for design.

## Develop
```bash
uv sync                 # create the env + install the workspace
uv run pytest           # all tests run against the emulator (no real frame needed)
uv run ruff check . && uv run mypy
```

## Try it
```bash
# Terminal 1 — a virtual frame
uv run memento-emulator --host 127.0.0.1 --name "Test Frame"

# Terminal 2 — talk to it
uv run memento discover --host 127.0.0.1
```
Against a real frame, use its IP (e.g. `uv run memento config <frame-ip>`); Wi-Fi credentials
the device exposes are redacted by the CLI and never persisted.

## License
MIT.

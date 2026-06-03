# memento-backend

FastAPI service that manages a **Memento Smart Frame** using photos from an **Immich** library.

- Browses Immich albums/assets.
- Prepares photos (EXIF-orient, scale, letterbox) to the frame's canvas.
- Uploads to the frame over the reverse-engineered LAN protocol (`memento-core`).
- Tracks what's synced in SQLite so re-syncs skip unchanged photos.
- Serves the built web UI as static files (single container).

All configuration is via environment variables — see [`.env.example`](../../.env.example).
Nothing is hardcoded to a particular frame, Immich instance, or host.

```bash
uv run memento-backend          # serves on $BIND_HOST:$BIND_PORT (default 0.0.0.0:8080)
# OpenAPI docs at /docs
```

Key endpoints (all under `/api`): `health`, `frame`, `frame/config`, `frame/next|previous`,
`immich/albums`, `immich/albums/{id}/assets`, `sync`, `photos`.

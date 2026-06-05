# slyde-backend

FastAPI service that manages a **Memento Smart Frame** using photos from an **Immich** library.

- Browses Immich albums/assets.
- Prepares photos (EXIF-orient, scale, letterbox) to the frame's canvas.
- Uploads to the frame over the reverse-engineered LAN protocol (`memento-core`).
- Tracks what's synced in SQLite so re-syncs skip unchanged photos.
- Serves the built web UI as static files (single container).

All configuration is via environment variables — see [`.env.example`](../../.env.example).
Nothing is hardcoded to a particular frame, Immich instance, or host.

```bash
uv run slyde-backend          # serves on $BIND_HOST:$BIND_PORT (default 0.0.0.0:8080)
# OpenAPI docs at /docs
```

Key endpoints (all under `/api`):
- `GET /frames` — discover frames (start screen); `GET /frames/{host}` — config
- `GET /frames/{host}/albums`, `POST /frames/{host}/albums` (create)
- `GET /frames/{host}/thumbnail/{image}` — thumbnail of an image on the frame
- `POST /frames/{host}/sync` — sync Immich assets (optional `target_album`)
- `POST /frames/{host}/upload` — direct multipart upload (optional `target_album`)
- `POST /frames/{host}/next|previous`, `PATCH /frames/{host}/config`,
  `DELETE /frames/{host}/photos/{filename}`
- `GET /immich/albums`, `GET /immich/albums/{id}/assets`, `GET /immich/assets/{id}/thumbnail`

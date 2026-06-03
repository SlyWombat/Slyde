# Example deployment: kdocker (Dockge, arm64)

This is **one example** of deploying Memento Manager — on a NanoPi M5 (`arm64`) running
[Dockge](https://github.com/louislam/dockge) as a Docker-Compose stack manager, with Immich
already running on the same host. None of this is required by the app; it's just a worked example.

## 1. Build the image (on the host, native arm64)
```bash
git clone <your fork> memento-manager && cd memento-manager
docker build -t memento-manager:latest .
```

## 2. Create the stack
```bash
sudo install -d -o "$USER" -g "$USER" /data/stacks/memento-manager
sudo install -d -o "$USER" -g "$USER" /data/memento/data
cp deploy/examples/kdocker/compose.yaml /data/stacks/memento-manager/compose.yaml
cp deploy/examples/kdocker/.env.example  /data/stacks/memento-manager/.env
# edit /data/stacks/memento-manager/.env — set IMMICH_API_KEY and confirm FRAME_HOST
```
Dockge picks the stack up automatically; start it from the UI or:
```bash
cd /data/stacks/memento-manager && docker compose up -d
```

## 3. Verify
```bash
curl -s http://localhost:8090/api/health
```
Open `http://<host>:8090/` for the UI.

## Notes
- **Networking:** with `FRAME_HOST` set, the default bridge network reaches the frame by unicast.
  To use UDP broadcast discovery instead, run the container with `network_mode: host`.
- **Immich:** reach it by the host's address (`http://<host-ip>:2283`) so no cross-stack network
  wiring is needed.
- **Memory:** the image is a single small Python process (FastAPI serving the SPA) — suitable for
  low-memory ARM boards.

# Memento Smart Frame — Reverse-Engineering & Local Control Plan

## Goal
Resurrect local control of the Memento Smart Frame (cloud service dead since ~2018-19)
by recovering its local network protocol and building a Python tool to manage photos on
the device over the LAN. End-user app form (CLI vs desktop vs web) decided after the
protocol is proven.

## Task #1 result — prior art search (DONE, 2026-06-03)
No one has publicly documented or built local control for the Memento frame. Community
threads (DPReview, Openframe forum, Dyxum, openframeio Google Group) only ever recommend
gutting the frame for a Raspberry Pi. **However**: firmware 6.02+ supports fully local
photo upload via the official iOS/Mac/Windows apps (no cloud) — confirming the local
socket protocol is viable. We are first to document it.

## Key reconnaissance findings
- Windows app `MementoSmartFrame.exe` is a **Unity** app by **Sarbakan** (Québec studio).
  All logic is in `MementoSmartFrame_Data/Managed/Assembly-CSharp.dll` (~1 MB, decompilable
  C#). `.dll.mdb` debug symbols present. Android APK (`Memento_6.0.apk`, OneDrive root) is a
  redundant second source.
- **Discovery**: UDP broadcast beacon — `EmitBroadcast`, `Beacon`, `DiscoverCadre`,
  `DiscoverySecure`/`DiscoveryUnsecure`. Frame found by broadcast/reply, not fixed IP.
- **Transport**: two sockets — `DataSocketState` (JSON control) + `FileSocketState`
  (binary image transfer). Lifecycle: ConnectNewFrame → InitiateConnectionWithFrame →
  ConnectToFrame → Connected → DisconnectFromFrame.
- **Commands**: CommandBroadcast, CommandChangeSetup, CommandControlFlow,
  CommandControlTransferFile, CommandTimer.
- **Encryption**: AES (`AesManaged`, `CreateDecryptor`, PBKDF2 `DeriveBytes`,
  `ENCRYPT_VERSION`). Traffic is encrypted — packet capture alone is insufficient; the
  key/IV derivation MUST be recovered from the DLL. This is why the source route is primary.
- **Framing**: length-prefixed (`ByteArrayToInt32`), file payloads chunked
  (`KEY_BIN_PART`/`KEY_BIN_REF`/`BytesInSequence`). Errors via `KEY_ERROR_ID`/`KEY_ERROR_INFO`.
- **Ops present**: add/remove/rename frame, albums (`MAX_ALBUMS`, `GenerateAlbumDataJSON`),
  images (`SendImages`/`SendPicture`/`MAX_IMAGES`), brightness/portrait/timers/away-schedule,
  frame time sync. Account/cloud layer exists but is the dead service — ignore; target the
  local socket protocol only.

## Decisions
- Reverse-engineering client language: **Python**.
- End-user app form: **decide later** (after Phase 3 proves control).

## STATUS (2026-06-03)
- **Phase 0 DONE**: DLL/APK copied to `reversing/bin/`; .NET 8 SDK + ilspycmd installed to
  `~/.dotnet`; `Assembly-CSharp.dll` decompiled to `reversing/decompiled/` (510 .cs files).
- **Phase 1 DONE**: full local protocol recovered from the `Cadre` namespace and written to
  `docs/protocol.md` (ports 2015-2018, AES+DES crypto incl. keys, discovery handshake, the
  `<type>|<json>|<id>|<EOF>` control framing, all command enums, raw file transfer on 2018).
- **Phase 3 STARTED**: Python package in `src/memento/` — `crypto.py` (AES+DES, self-test
  passes), `discovery.py`, `cli.py`. Run: `PYTHONPATH=src python3 -m memento.cli selftest|discover`.
  Needs `cryptography` (installed to user site via `pip install --user --break-system-packages`).
- **Phase 2 DONE (read path)**: connected live to frame "Living Room" @ **192.168.10.113**
  (fw 6.02, 35", landscape) over Tailscale (home LAN 192.168.10.0/24 via opnsense subnet
  router). GetConfig + GetFrameTime decrypt correctly — DES key, framing, and command enums
  all confirmed. Broadcast discovery does NOT cross Tailscale; found the frame by TCP-scanning
  2017/2018 and connecting by unicast.
- **Phase 3 (read) DONE**: `src/memento/client.py` `MementoFrame` class talks to the frame
  (get_config / get_frame_time / next/prev image / current image). Image **upload** (WriteFile
  over 2018) is implemented in the protocol doc but NOT yet coded/tested — that's the next proof.
- Deployment target: runs eventually on **kdocker** alongside other local apps (creds in the
  SlyClaw project). For now testing runs from WSL/Windows as convenient.

## BUILD STATUS (2026-06-03, cont.)
Scope expanded by user into a productized, reusable app (see `docs/architecture.md`):
- Decisions: Python/FastAPI backend, React+TS+Vite+Tailwind UI, Immich source (configurable),
  Docker/Compose deploy (kdocker = NanoPi M5 arm64 via Dockge; example only). **Nothing hardcoded.**
- DONE: monorepo scaffold (uv workspace, ruff+mypy-strict+pytest, CI, pre-commit);
  `memento-core` library (crypto, discovery, control, file transfer, upload); `memento-emulator`
  (faithful server-side frame). 18 tests pass against the emulator; mypy/ruff clean.
- NEXT: backend (FastAPI + Immich + image pipeline), web UI, containerization/deploy.
- Tooling: `uv` at `~/.local/bin/uv`; `~/.dotnet` has the SDK+ilspycmd for re-decompiling.

## Phases

### Phase 0 — Tooling & safe copies
- Copy `Assembly-CSharp.dll` (+ `.mdb`) and `Memento_6.0.apk` into project (never edit OneDrive originals).
- Install ILSpy CLI (`dotnet tool install -g ilspycmd`) or dnSpy; decompile DLL to C# project.
- Install `jadx`/`apktool` as APK backup source.

### Phase 1 — Recover protocol from source (PRIMARY)
- Read networking classes: discovery/beacon, Data + File socket state machines, command/JSON
  layer, and AES key derivation (password, salt, iterations, key size, IV from `DeriveBytes`).
- Produce `docs/protocol.md`: discovery handshake, ports, frame format (length + opcode +
  encrypted JSON), per-command request/response schemas, file-chunking scheme, error codes.

### Phase 2 — Live validation against the real device
- Locate frame on LAN (ARP scan / app broadcast). Capture traffic (Wireshark/tcpdump) during
  real app operations; confirm ports/framing and that our decrypted plaintext matches the doc.

### Phase 3 — Reference client (go/no-go milestone)
- Python CLI: discover → connect → authenticate/encrypt → list albums → upload one image E2E.
- Image landing on the frame from our own code = protocol owned.

### Phase 4 — Management application
- Build real photo-management app on the validated client library: discover, browse/create
  albums, upload/delete/reorder, display options (brightness, portrait, timers).
  Pick UI form (CLI / Tauri-Electron desktop / web+local-agent) here.

### Phase 5 — Docs & packaging
- Finalize protocol spec + Python library, README, publish openly (community demand exists).

## Resource paths
- Windows app DLL: `/mnt/d/OneDrive/Software/memento/Memento SmartFrame/MementoSmartFrame_Data/Managed/Assembly-CSharp.dll`
- Android APK: `/mnt/d/OneDrive/Memento_6.0.apk`
- Bundled Windows app also ships a `Photos-001 (1)` sample set.

"""Run a standalone emulated frame (protocol + optional web UI), for manual testing / dev."""

from __future__ import annotations

import argparse
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path

from memento_core.protocol import DEFAULT_PORTS

from . import __version__
from .server import EmulatedFrame
from .state import FrameState
from .web import EmulatorWeb


def _detect_ip() -> str:
    """Best-effort primary IPv4 of this host (the address clients would connect to)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))  # TEST-NET-1; no packets actually sent
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _bundle_version(app_dir: Path | None) -> str | None:
    """Version of the staged OTA bundle (the VERSION file the bundle ships), if any."""
    if app_dir is not None and (version_file := app_dir / "VERSION").is_file():
        return version_file.read_text().strip() or None
    return None


def _make_on_update(app_dir: Path | None) -> Callable[[str, str], object] | None:
    """Self-update handler for a real device: stage the bundle into ``app_dir`` then exit so the
    service manager relaunches it (``app_dir`` is first on PYTHONPATH). None when not set."""
    if app_dir is None:
        return None
    from .updater import apply_update

    def on_update(url: str, md5: str) -> None:
        apply_update(url, md5, target_dir=app_dir, restart=lambda: os._exit(0))

    return on_update


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memento-emulator", description="Emulate a Memento Smart Frame on the network."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address for the TCP/UDP channels")
    parser.add_argument("--name", default="Test Frame", help="Frame display name")
    parser.add_argument("--web-host", default="0.0.0.0", help="Bind address for the web UI")
    parser.add_argument("--web-port", type=int, default=8099, help="Web UI port (0 to disable)")
    parser.add_argument(
        "--advertise-ip", default="", help="IP to advertise/display (default: auto-detect)"
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("MEMENTO_EMULATOR_DATA", ""),
        help="Persist config/photos/albums here so they survive a restart (default: in-memory)",
    )
    parser.add_argument(
        "--mode",
        choices=["emulator", "display"],
        default=os.environ.get("MEMENTO_MODE", "emulator"),
        help="emulator: headless protocol + web UI. display: also render fullscreen (e.g. a Pi).",
    )
    args = parser.parse_args(argv)

    advertise_ip = args.advertise_ip or _detect_ip()
    data_dir = Path(args.data_dir) if args.data_dir else None
    app_dir = Path(os.environ["MEMENTO_APP_DIR"]) if os.environ.get("MEMENTO_APP_DIR") else None
    state = FrameState(name=args.name, ip=advertise_ip, data_dir=data_dir)
    # Report this soft-frame's own firmware version: the staged OTA bundle's VERSION if running
    # from one, otherwise our package version. (This is what the manager's OTA check compares.)
    state.update_config({"SoftwareVersion": _bundle_version(app_dir) or __version__})
    frame = EmulatedFrame(
        state, host=args.host, ports=DEFAULT_PORTS, on_update=_make_on_update(app_dir)
    ).start()
    print(
        f"Emulated frame '{args.name}' at {advertise_ip} "
        f"(udp {DEFAULT_PORTS.broadcast}/{DEFAULT_PORTS.broadcast_response}, "
        f"tcp {DEFAULT_PORTS.control}/{DEFAULT_PORTS.file})."
    )
    print(f"State: {'persisted at ' + str(data_dir) if data_dir else 'in-memory (not persisted)'}.")
    web: EmulatorWeb | None = None
    if args.web_port:
        web = EmulatorWeb(
            state, host=args.web_host, port=args.web_port, ports=DEFAULT_PORTS
        ).start()
        print(f"Web UI: http://{advertise_ip}:{web.port}/  (Ctrl-C to stop)")

    try:
        if args.mode == "display":
            from .renderer import Renderer  # lazy: needs pygame (the 'display' extra)

            print("Display: fullscreen renderer (Esc or Ctrl-C to stop).")
            Renderer(state).run()  # blocks on the main thread until quit
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if web:
            web.stop()
        frame.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

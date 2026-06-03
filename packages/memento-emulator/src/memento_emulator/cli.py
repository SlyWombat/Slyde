"""Run a standalone emulated frame (protocol + optional web UI), for manual testing / dev."""

from __future__ import annotations

import argparse
import socket
import time

from memento_core.protocol import DEFAULT_PORTS

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
    args = parser.parse_args(argv)

    advertise_ip = args.advertise_ip or _detect_ip()
    state = FrameState(name=args.name, ip=advertise_ip)
    frame = EmulatedFrame(state, host=args.host, ports=DEFAULT_PORTS).start()
    print(
        f"Emulated frame '{args.name}' at {advertise_ip} "
        f"(udp {DEFAULT_PORTS.broadcast}/{DEFAULT_PORTS.broadcast_response}, "
        f"tcp {DEFAULT_PORTS.control}/{DEFAULT_PORTS.file})."
    )
    web: EmulatorWeb | None = None
    if args.web_port:
        web = EmulatorWeb(
            state, host=args.web_host, port=args.web_port, ports=DEFAULT_PORTS
        ).start()
        print(f"Web UI: http://{advertise_ip}:{web.port}/  (Ctrl-C to stop)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if web:
            web.stop()
        frame.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

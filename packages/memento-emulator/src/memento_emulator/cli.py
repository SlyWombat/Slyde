"""Run a standalone emulated frame (for manual testing / dev)."""

from __future__ import annotations

import argparse
import time

from memento_core.protocol import DEFAULT_PORTS

from .server import EmulatedFrame
from .state import FrameState


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memento-emulator", description="Emulate a Memento Smart Frame on the network."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address for TCP channels")
    parser.add_argument("--name", default="Test Frame", help="Frame display name")
    args = parser.parse_args(argv)

    state = FrameState(name=args.name, ip=args.host)
    frame = EmulatedFrame(state, host=args.host, ports=DEFAULT_PORTS).start()
    print(
        f"Emulated frame '{args.name}' on {args.host} "
        f"(udp {DEFAULT_PORTS.broadcast}/{DEFAULT_PORTS.broadcast_response}, "
        f"tcp {DEFAULT_PORTS.control}/{DEFAULT_PORTS.file}). Ctrl-C to stop."
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        frame.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line interface for memento-core."""

from __future__ import annotations

import argparse
import sys

from . import crypto
from .client import FrameClient
from .discovery import discover


def _cmd_discover(args: argparse.Namespace) -> int:
    frames = discover(host=args.host, timeout=args.timeout)
    if not frames:
        print("No frames found.")
        return 1
    for f in frames:
        print(
            f"{f.name}  @ {f.ip}  (mac {f.mac or '?'})  "
            f'fw{f.softver} hw{f.hardver} {f.size}" {f.orientation}'
        )
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    with FrameClient(args.host) as frame:
        cfg = frame.get_config()
    redacted = {k: ("***" if "WiFi" in k else v) for k, v in cfg.items()}
    for k, v in redacted.items():
        print(f"{k}: {v}")
    return 0


def _cmd_upload(args: argparse.Namespace) -> int:
    with FrameClient(args.host) as frame:
        frame.upload_file(args.path, args.dest)
    print(f"uploaded {args.path} -> {args.dest or args.path}")
    return 0


def _cmd_selftest(_: argparse.Namespace) -> int:
    ok = crypto.aes_decrypt(crypto.aes_encrypt("x")) == "x"
    ok &= crypto.des_decrypt(crypto.des_encrypt("{}")) == "{}"
    print("crypto round-trip:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memento", description="Control a Memento Smart Frame.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pd = sub.add_parser("discover", help="Find frames on the LAN")
    pd.add_argument("--host", default="255.255.255.255")
    pd.add_argument("--timeout", type=float, default=6.0)
    pd.set_defaults(func=_cmd_discover)

    pc = sub.add_parser("config", help="Print a frame's config (Wi-Fi creds redacted)")
    pc.add_argument("host")
    pc.set_defaults(func=_cmd_config)

    pu = sub.add_parser("upload", help="Upload an image to a frame")
    pu.add_argument("host")
    pu.add_argument("path")
    pu.add_argument("--dest", default=None)
    pu.set_defaults(func=_cmd_upload)

    sub.add_parser("selftest", help="Verify the ciphers round-trip").set_defaults(
        func=_cmd_selftest
    )

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

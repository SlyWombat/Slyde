#!/usr/bin/env python3
"""Live smoke test for the SwitchBot AI Art Frame client (#64).

Reads SWITCHBOT_TOKEN / SWITCHBOT_SECRET from the environment (never prints them) and exercises the
official OpenAPI: list devices, show every AI Art Frame's status. With ``--push <image>`` it also
sends one test image via ``uploadImage`` to confirm render/latency on the panel.

Run it with the existing SlyClaw/HA credentials sourced, e.g.:
    set -a; . ".../HomeAssistant/secrets/SlyClaw/ecowitt-switchbot.env"; set +a
    uv run python scripts/switchbot_smoke.py            # read-only
    uv run python scripts/switchbot_smoke.py --push pic.jpg   # + push one image
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from slyde_backend.switchbot import ART_FRAME, SwitchBotClient, SwitchBotError


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a .env into os.environ (without overriding what's already set).

    Read by Python at runtime — values never pass through the shell, so they stay out of any
    command transcript.
    """
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


async def main() -> int:
    _load_env_file(Path(__file__).resolve().parent.parent / ".env")

    ap = argparse.ArgumentParser()
    ap.add_argument("--push", metavar="IMAGE", help="also push this JPEG to the first AI Art Frame")
    ap.add_argument("--device", help="target a specific deviceId (default: first AI Art Frame)")
    args = ap.parse_args()

    token, secret = os.environ.get("SWITCHBOT_TOKEN"), os.environ.get("SWITCHBOT_SECRET")
    if not token or not secret:
        print("ERROR: set SWITCHBOT_TOKEN and SWITCHBOT_SECRET (source the HA/SlyClaw env).")
        return 2

    async with SwitchBotClient(token, secret) as c:
        try:
            devices = await c.list_devices()
        except SwitchBotError as e:
            print(f"API error listing devices: {e}")
            return 1

        print(f"Account devices: {len(devices)}")
        frames = [d for d in devices if d.device_type == ART_FRAME]
        for d in frames:
            print(f"  • AI Art Frame  id={d.device_id}  name={d.name!r}  hub={d.hub_id or '-'}")
        if not frames:
            print("No AI Art Frame found on the account — is it paired and online?")
            return 1

        for d in frames:
            s = await c.art_frame_status(d.device_id)
            print(
                f"  status {d.device_id}: battery={s.battery}%  "
                f"mode={'slideshow' if s.display_mode else 'static'}  "
                f"fw={s.version or '?'}  current={s.image_url or '(none)'}"
            )

        if args.push:
            target = args.device or frames[0].device_id
            img = Path(args.push)
            if not img.is_file():
                print(f"--push: no such file: {img}")
                return 2
            print(f"Pushing {img.name} ({img.stat().st_size} bytes) to {target} …")
            await c.upload_image_bytes(target, img.read_bytes())
            print(
                "uploadImage accepted — watch the frame; check `current` on the next status read."
            )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

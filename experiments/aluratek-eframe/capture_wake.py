"""Passive wake capture for the Aluratek/Sungale eFrame (#9), via the OPNsense packet-capture API.

Sniffs the frame's plain-HTTP wake exchange at the gateway — LAN interface, filtered to the cloud
server IP — so we get the exact endpoints + request/response bodies the frame uses. This is PASSIVE:
it only runs tcpdump on OPNsense; nothing is ever sent to the frame.

The frame wakes ~every 2 days at ~05:00 UTC (confirmed via AdGuard Home DNS logs), DHCP IP rotates,
and it resolves + hits ``us.xiaowooya.eframe.sungale.com.cn`` (47.88.4.176, plain HTTP).

Usage:
  python capture_wake.py start      # configure + start the capture (leave running across the wake)
  python capture_wake.py status     # is it running? how many packets so far
  python capture_wake.py view       # tcpdump-style text view of what's captured
  python capture_wake.py download   # save the pcap locally (capture_wake.pcap)
  python capture_wake.py stop       # stop the capture

Creds: read from ../../../OpnSense/config.json (the sibling OPNsense project) or env
OPNSENSE_URL / OPNSENSE_KEY / OPNSENSE_SECRET.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

CLOUD_IP = "47.88.4.176"  # us.xiaowooya.eframe.sungale.com.cn (Sungale US cloud)
FRAME_IP = (
    "192.168.10.127"  # the eFrame's current DHCP lease (post-cutover it talks to Slyde @ .11)
)
LAN_IF = "igc1"  # OPNsense LAN interface (where the frame's traffic ingresses)
DESC = "eframe-wake"  # capture description/tag
# Post-cutover we capture the FRAME's whole conversation (DNS + HTTP) to see where it tries to fetch
# the image — host=the frame, protocol=any (DNS is UDP). Pre-cutover used host=CLOUD_IP, TCP.
CAP_HOST = FRAME_IP
CAP_PROTO = "any"


def _creds() -> tuple[str, str, str]:
    if os.environ.get("OPNSENSE_URL"):
        return (
            os.environ["OPNSENSE_URL"].rstrip("/"),
            os.environ["OPNSENSE_KEY"],
            os.environ["OPNSENSE_SECRET"],
        )
    cfg = Path(__file__).resolve().parents[3] / "OpnSense" / "config.json"
    o = json.loads(cfg.read_text())["opnsense"]
    return o["base_url"].rstrip("/"), o["api_key"], o["api_secret"]


class OPN:
    def __init__(self) -> None:
        self.base, key, secret = _creds()
        self.auth = (key, secret)

    def get(self, path: str) -> dict:
        r = requests.get(f"{self.base}/{path}", auth=self.auth, verify=False, timeout=15)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict | None = None) -> dict:
        r = requests.post(
            f"{self.base}/{path}", json=body or {}, auth=self.auth, verify=False, timeout=30
        )
        r.raise_for_status()
        return r.json()

    def raw(self, path: str) -> bytes:
        r = requests.get(f"{self.base}/{path}", auth=self.auth, verify=False, timeout=60)
        r.raise_for_status()
        return r.content


def _jobs(opn: OPN) -> list[dict]:
    return opn.get("diagnostics/packet_capture/searchJobs").get("rows", [])


def _eframe_job(opn: OPN) -> str | None:
    """The eframe-wake capture job id (jobs are keyed by ``id``)."""
    for row in _jobs(opn):
        if row.get("description") == DESC:
            return row["id"]
    return None


def start(opn: OPN) -> None:
    settings = {
        "interface": LAN_IF,
        "protocol": CAP_PROTO,
        "host": CAP_HOST,  # the frame's whole conversation (DNS+HTTP) to see image fetches
        # All ports (empty = any): the frame's port is unconfirmed and the phone app uses 8080
        # (plain HTTP) for photo/upload + image_library/push — port "80" alone would miss both.
        "port": "",
        "count": "10000",  # high cap; filtered traffic is near-zero until the wake
        "snaplen": "",  # empty = full packets (we need the HTTP bodies); "0" is rejected
        "promiscuous": "0",
        "fam": "any",
        "protocol_not": "0",
        "port_not": "0",
        "description": DESC,
    }
    res = opn.post("diagnostics/packet_capture/set", {"packetcapture": {"settings": settings}})
    jid = res["uuid"]  # set creates the job and returns its id
    print("set:", res)
    print("start:", opn.post(f"diagnostics/packet_capture/start/{jid}"))
    print("\nCapture running on OPNsense (LAN igc1, host", CLOUD_IP, "tcp/80), job", jid)
    print("Leave it; the frame wakes ~05:00 UTC every ~2 days. Then: download.")


def status(opn: OPN) -> None:
    print(json.dumps(_jobs(opn), indent=1))


def view(opn: OPN) -> None:
    jid = _eframe_job(opn)
    if not jid:
        print("no eframe-wake capture job found")
        return
    print(json.dumps(opn.get(f"diagnostics/packet_capture/view/{jid}"), indent=1)[:4000])


def download(opn: OPN) -> None:
    jid = _eframe_job(opn)
    if not jid:
        print("no eframe-wake capture job found")
        return
    out = Path(__file__).with_name("capture_wake.pcap")
    out.write_bytes(opn.raw(f"diagnostics/packet_capture/download/{jid}"))
    print(f"saved {out} ({out.stat().st_size} bytes) — open in Wireshark / tshark -r")


def stop(opn: OPN) -> None:
    jid = _eframe_job(opn)
    if not jid:
        print("no eframe-wake capture job found")
        return
    print("stop:", opn.post(f"diagnostics/packet_capture/stop/{jid}"))


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    opn = OPN()
    {"start": start, "status": status, "view": view, "download": download, "stop": stop}.get(
        cmd, status
    )(opn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

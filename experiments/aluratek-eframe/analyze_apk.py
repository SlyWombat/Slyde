"""Static APK recon — pure Python (no Java/jadx needed).

Usage:  python analyze_apk.py <app.apk>

Dumps the things that decide the revival strategy for a cloud-dependent frame:
  - package / version / SDK / signing
  - network-relevant permissions
  - network_security_config (does it trust USER CAs → MITM-able? or pin?)
  - every http(s) URL + candidate hostname baked into the code/assets
  - TLS-pinning indicators (OkHttp CertificatePinner, sha256/ pins, TrustManagers)
  - firmware / OTA / update strings (the no-hardware reflash lead)

Reads manifest/permissions/NSC via androguard; greps raw DEX/.so/asset bytes for
strings (robust across androguard versions). Decompile with jadx for full logic.
"""

from __future__ import annotations

import re
import sys
import zipfile
from collections import Counter

try:  # androguard logs at DEBUG via loguru — silence it so the report is readable
    from loguru import logger as _loguru

    _loguru.remove()
except Exception:  # noqa: BLE001
    pass

URL_RE = re.compile(rb"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{4,}")
HOST_RE = re.compile(rb"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b")
PIN_HINTS = [
    b"CertificatePinner", b"sha256/", b"setCertificatePinner", b"X509TrustManager",
    b"TrustManagerFactory", b"checkServerTrusted", b"HostnameVerifier",
    b"networkSecurityConfig", b"pinning", b"okhttp3",
]
OTA_HINTS = [
    b"firmware", b"/ota", b"ota/", b"upgrade", b"update", b".bin", b"checkVersion",
    b"newVersion", b"firmwareUrl", b"mcu", b"flash",
]
NOISE = re.compile(rb"(schemas\.android\.com|w3\.org|apache\.org|googleapis\.com/"
                   rb"|gstatic|crashlytics|google-analytics|fonts\.google|example\.com"
                   rb"|android\.com|github\.com/square|bumptech|json-schema)")


def grep(blobs: dict[str, bytes], pattern: re.Pattern[bytes]) -> Counter[str]:
    hits: Counter[str] = Counter()
    for data in blobs.values():
        for m in pattern.findall(data):
            hits[m.decode("utf-8", "replace")] += 1
    return hits


def main(path: str) -> int:
    print(f"\n=== APK static recon: {path} ===\n")

    # --- code/asset blobs we string-search ---
    blobs: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.endswith((".dex", ".so")) or n.startswith(("assets/", "res/raw")):
                try:
                    blobs[n] = z.read(n)
                except Exception:  # noqa: BLE001 - best-effort recon
                    pass

    # --- manifest / permissions / NSC via androguard ---
    try:
        from androguard.core.apk import APK

        a = APK(path)
        print(f"package : {a.get_package()}")
        print(f"version : {a.get_androidversion_name()} (code {a.get_androidversion_code()})")
        print(f"minSdk  : {a.get_min_sdk_version()}   targetSdk: {a.get_target_sdk_version()}")
        net_perms = [p for p in a.get_permissions()
                     if any(k in p for k in ("INTERNET", "NETWORK", "WIFI"))]
        print(f"net perms: {', '.join(net_perms) or '(none)'}")
        print(f"cleartextTraffic: {a.get_attribute_value('application', 'usesCleartextTraffic')}")

        nsc_ref = a.get_attribute_value("application", "networkSecurityConfig")
        print(f"networkSecurityConfig ref: {nsc_ref or '(none declared)'}")
        from androguard.core.axml import AXMLPrinter

        for n in [x for x in a.get_files() if "security_config" in x or "network_sec" in x]:
            try:
                xml = AXMLPrinter(a.get_file(n)).get_xml().decode("utf-8", "replace")
                print(f"\n--- {n} ---\n{xml.strip()}\n")
            except Exception as e:  # noqa: BLE001
                print(f"(could not decode {n}: {e})")
    except Exception as e:  # noqa: BLE001
        print(f"(androguard manifest parse failed: {e}; continuing with string recon)")

    # --- URLs ---
    urls = grep(blobs, URL_RE)
    interesting = {u: c for u, c in urls.items() if not NOISE.search(u.encode())}
    print(f"\n--- http(s) URLs ({len(urls)} total, {len(interesting)} non-noise) ---")
    for u, c in sorted(interesting.items(), key=lambda kv: -kv[1])[:40]:
        print(f"  [{c:>3}] {u}")

    # --- candidate API hostnames (from URLs) ---
    hosts: Counter[str] = Counter()
    for u in interesting:
        m = re.match(r"https?://([^/:]+)", u)
        if m:
            hosts[m.group(1)] += interesting[u]
    print(f"\n--- candidate cloud hosts ---")
    for h, c in hosts.most_common(20):
        print(f"  [{c:>3}] {h}")

    # --- pinning posture ---
    print(f"\n--- TLS pinning / trust indicators ---")
    any_pin = False
    for hint in PIN_HINTS:
        n = sum(d.count(hint) for d in blobs.values())
        if n:
            any_pin = True
            print(f"  {hint.decode():<22} x{n}")
    if not any_pin:
        print("  (none found — good sign for MITM, but verify with jadx)")

    # --- OTA / firmware leads ---
    print(f"\n--- firmware / OTA strings ---")
    for hint in OTA_HINTS:
        n = sum(d.lower().count(hint) for d in blobs.values())
        if n:
            print(f"  {hint.decode():<14} x{n}")

    print("\nNext: jadx -d out_jadx <apk>  then read the hosts above in context.\n")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))

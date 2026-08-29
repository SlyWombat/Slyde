#!/usr/bin/env python3
"""Re-derive every artefact the firmware teardown (#75) cites, from the OTA zip.

    /usr/bin/python3 reversing/tools/teardown.py \
        ../memento-firmware/Memento_35.zip \
        --out reversing/firmware-teardown --work /tmp/fw

Everything except the CIL disassembly runs on stdlib plus `lzallright` (the
kernel is LZO-compressed and this box has no `lzop`). The CIL step needs
`dnfile` and is done by `cildasm.py`; run that separately if the import fails.

Use a LINUX python (`/usr/bin/python3`) -- `python3` on this WSL box's PATH is
Windows Python and rewrites text files as CRLF/cp1252.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import certs  # noqa: E402
import fwlib  # noqa: E402
import ucl  # noqa: E402

EXPECTED_MD5 = "57a7151d932a11f603e44c44fcb49968"

# Where the UCL container holding U-Boot proper starts inside bootloader.img.
UBOOT_CONTAINER_OFF = 0x8000

# Amlogic `struct partitions`: char name[16]; u64 size; u64 offset; u32 mask.
PART_ENTRY = 40
PART_TABLES = {"media (pre-user)": 0xAEEB8, "user": 0xB0000}


def sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path, help="Memento_35.zip")
    ap.add_argument("--out", type=Path, required=True, help="derived text artefacts")
    ap.add_argument("--work", type=Path, required=True, help="scratch for large blobs")
    args = ap.parse_args()

    out, work = args.out, args.work
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    log: list[str] = []

    def say(s: str) -> None:
        print(s)
        log.append(s)

    # -- 0. identity ------------------------------------------------------
    digest = hashlib.md5(args.package.read_bytes()).hexdigest()
    say(f"package  {args.package.name}  md5 {digest}"
        f"  {'OK' if digest == EXPECTED_MD5 else 'MISMATCH -- different bytes!'}")

    fw = work / "fw"
    if not (fw / "bootloader.img").exists():
        with zipfile.ZipFile(args.package) as z:
            z.extractall(fw)
    say(f"extracted to {fw}")

    # -- 1. certificates --------------------------------------------------
    lines = []
    for label, rel in [
        ("OTA package signature (META-INF/CERT.RSA)", "META-INF/CERT.RSA"),
        ("device trust store (system/etc/security/otacerts.zip)",
         "system/etc/security/otacerts.zip"),
        ("package copy (META-INF/com/android/otacert)", "META-INF/com/android/otacert"),
    ]:
        lines.append(f"### {label}")
        for c in certs.certs_from(fw / rel):
            for k, v in certs.describe(c).items():
                lines.append(f"    {k:12s} {v}")
        lines.append("")
    for apk in ["system/app/Cadre.apk", "system/app/Home.apk",
                "system/app/OTAUpgrade.apk", "system/priv-app/Settings.apk"]:
        with zipfile.ZipFile(fw / apk) as z:
            for n in z.namelist():
                if n.upper().endswith((".RSA", ".DSA", ".EC")):
                    for c in certs.certs_from_bytes(z.read(n)):
                        d = certs.describe(c)
                        lines.append(f"### {apk} ({n})")
                        lines.append(f"    sha256       {d['sha256']}")
                        lines.append(f"    subject      {d['subject']}")
                        lines.append("")
    (out / "certificates.txt").write_text("\n".join(lines))
    say("wrote certificates.txt")

    # -- 2. bootloader ----------------------------------------------------
    bl = (fw / "bootloader.img").read_bytes()
    uboot = ucl.unpack_container(bl, UBOOT_CONTAINER_OFF)
    (work / "uboot.bin").write_bytes(uboot)
    say(f"u-boot   {len(uboot)} bytes (UCL nrv2d, container @0x{UBOOT_CONTAINER_OFF:x})")

    ustr = list(fwlib.strings(uboot, 4))
    (work / "uboot-strings.txt").write_text(
        "\n".join(f"{o:08x}  {t}" for o, t in ustr))

    env = [t for _, t in ustr if "=" in t and t[0].isalpha()
           and t.split("=")[0].replace("_", "").isalnum()]
    start = next(i for i, t in enumerate(env) if t.startswith("bootcmd="))
    (out / "uboot-default-env.txt").write_text("\n".join(env[start:]) + "\n")
    say("wrote uboot-default-env.txt")

    parts = []
    for label, base in PART_TABLES.items():
        parts.append(f"### {label} table @0x{base:x}")
        parts.append(f"{'name':16s} {'size':>16s} {'offset':>12s}  mask")
        for i in range(16):
            e = uboot[base + i * PART_ENTRY : base + (i + 1) * PART_ENTRY]
            name = e[:16].rstrip(b"\0").decode("latin1", "replace")
            if not name or not name.isprintable() or " " in name:
                break
            size, off = struct.unpack("<QQ", e[16:32])
            (mask,) = struct.unpack("<I", e[32:36])
            sz = "AUTO" if size == (1 << 64) - 1 else f"{size // (1 << 20)} MiB"
            parts.append(f"{name:16s} {sz:>16s} {off:>12d}  {mask}")
        parts.append("")
    (out / "emmc-partitions.txt").write_text("\n".join(parts))
    say("wrote emmc-partitions.txt")

    # -- 3. boot.img ------------------------------------------------------
    bootimg = (fw / "boot.img").read_bytes()
    b = fwlib.BootImage(bootimg)
    say(f"boot.img sha1 {sha1(bootimg)} ({len(bootimg)} bytes)")
    (out / "boot-img.txt").write_text(
        b.summary() + f"\nsha1         {sha1(bootimg)}\nsize         {len(bootimg)}\n")

    (work / "kernel.uimg").write_bytes(b.kernel)
    (work / "ramdisk.gz").write_bytes(b.ramdisk)

    # the DTB rides in the "second" slot
    dtb = b.second
    (work / "dtb.bin").write_bytes(dtb)
    (out / "boot.dts").write_text(fwlib.dtb_to_dts(dtb) + "\n")
    say("wrote boot.dts")

    cpio = fwlib.gunzip_at(b.ramdisk, 0)
    fwlib.extract_cpio(cpio, work / "ramdisk")
    say(f"ramdisk  {len(cpio)} bytes -> {work / 'ramdisk'}")

    # kernel: u-boot uImage wrapping an lzop stream
    try:
        vmlinux = unpack_uimage_lzo(b.kernel)
    except ImportError:
        say("kernel   SKIPPED -- pip install lzallright")
        vmlinux = None
    if vmlinux:
        (work / "vmlinux.bin").write_bytes(vmlinux)
        i = vmlinux.find(b"Linux version")
        banner = vmlinux[i : i + 200].split(b"\0")[0].decode("latin1")
        say(f"kernel   {len(vmlinux)} bytes -- {banner}")
        cfg = fwlib.extract_ikconfig(vmlinux)
        if cfg:
            (out / "kernel.config").write_bytes(cfg)
            say("wrote kernel.config")
        (work / "vmlinux-strings.txt").write_text(
            "\n".join(t for _, t in fwlib.strings(vmlinux, 6)))

    (work / "teardown.log").write_text("\n".join(log) + "\n")
    return 0


def unpack_uimage_lzo(uimg: bytes) -> bytes:
    """Unwrap a 64-byte u-boot uImage whose payload is an lzop stream."""
    import lzallright

    (size,) = struct.unpack(">I", uimg[12:16])
    payload = uimg[64 : 64 + size]
    p = payload.index(b"\x89LZO\x00\r\n\x1a\n") + 9
    (ver,) = struct.unpack(">H", payload[p : p + 2])
    p += 4                      # version, libversion
    if ver >= 0x0940:
        p += 2                  # version needed to extract
    p += 2                      # method, level
    (flags,) = struct.unpack(">I", payload[p : p + 4])
    p += 4
    if flags & 0x800:
        p += 4                  # filter
    p += 12                     # mode, mtime, gmtdiff
    p += 1 + payload[p]         # name
    p += 4                      # header checksum
    chunks = []
    while p + 8 <= len(payload):
        (dlen,) = struct.unpack(">I", payload[p : p + 4])
        p += 4
        if dlen == 0:
            break
        (slen,) = struct.unpack(">I", payload[p : p + 4])
        p += 4
        if flags & 0x1:
            p += 4              # uncompressed adler32
        if flags & 0x2:
            p += 4              # compressed adler32
        blk = payload[p : p + slen]
        p += slen
        chunks.append(blk if slen == dlen
                      else lzallright.LZOCompressor.decompress(blk, output_size_hint=dlen))
    return b"".join(chunks)


if __name__ == "__main__":
    sys.exit(main())

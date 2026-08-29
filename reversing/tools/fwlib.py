"""Shared helpers for the vendor-firmware teardown (issue #75).

Pure stdlib, no external tools: the box has no binwalk/abootimg/unzip/xxd.
Run with a Linux python3 (/usr/bin/python3) -- `python3` on the WSL PATH is
Windows Python and mangles line endings.
"""

from __future__ import annotations

import gzip
import io
import re
import struct
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# generic binary inspection
# --------------------------------------------------------------------------


def strings(data: bytes, minlen: int = 6, encoding: str = "ascii"):
    """Yield (offset, text) for printable runs, like strings(1)."""
    if encoding == "ascii":
        pat = rb"[\x20-\x7e]{%d,}" % minlen
        for m in re.finditer(pat, data):
            yield m.start(), m.group().decode("ascii")
    elif encoding == "utf-16le":
        pat = rb"(?:[\x20-\x7e]\x00){%d,}" % minlen
        for m in re.finditer(pat, data):
            yield m.start(), m.group().decode("utf-16le")
    else:  # pragma: no cover
        raise ValueError(encoding)


def hexdump(data: bytes, base: int = 0, limit: int | None = None) -> str:
    out = []
    if limit is not None:
        data = data[:limit]
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk).ljust(47)
        txt = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        out.append(f"{base + off:08x}  {hexpart}  |{txt}|")
    return "\n".join(out)


def find_all(data: bytes, needle: bytes):
    start = 0
    while True:
        i = data.find(needle, start)
        if i < 0:
            return
        yield i
        start = i + 1


# --------------------------------------------------------------------------
# Android boot image (boot.img / recovery.img)
# --------------------------------------------------------------------------

BOOT_MAGIC = b"ANDROID!"


class BootImage:
    """Parser for the classic (pre-v3) Android boot image header."""

    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = offset
        h = data[offset : offset + 1632]
        if h[:8] != BOOT_MAGIC:
            raise ValueError("not an Android boot image")
        (
            self.kernel_size,
            self.kernel_addr,
            self.ramdisk_size,
            self.ramdisk_addr,
            self.second_size,
            self.second_addr,
            self.tags_addr,
            self.page_size,
            self.unused0,
            self.unused1,
        ) = struct.unpack("<10I", h[8:48])
        self.name = h[48:64].rstrip(b"\0").decode("latin1")
        self.cmdline = h[64:576].rstrip(b"\0").decode("latin1")
        self.id = h[576:608]
        self.extra_cmdline = h[608:2656 - 2048].rstrip(b"\0").decode("latin1")

    def _slice(self, index: int, size: int) -> bytes:
        ps = self.page_size
        start = self.offset + ps  # header occupies page 0
        # preceding sections, each padded to a page boundary
        sizes = [self.kernel_size, self.ramdisk_size, self.second_size]
        for prev in sizes[:index]:
            start += ((prev + ps - 1) // ps) * ps
        return self.data[start : start + size]

    @property
    def kernel(self) -> bytes:
        return self._slice(0, self.kernel_size)

    @property
    def ramdisk(self) -> bytes:
        return self._slice(1, self.ramdisk_size)

    @property
    def second(self) -> bytes:
        return self._slice(2, self.second_size)

    def summary(self) -> str:
        return "\n".join(
            [
                f"page_size    {self.page_size}",
                f"kernel       {self.kernel_size} bytes @ 0x{self.kernel_addr:08x}",
                f"ramdisk      {self.ramdisk_size} bytes @ 0x{self.ramdisk_addr:08x}",
                f"second       {self.second_size} bytes @ 0x{self.second_addr:08x}",
                f"tags_addr    0x{self.tags_addr:08x}",
                f"name         {self.name!r}",
                f"cmdline      {self.cmdline!r}",
                f"id           {self.id[:20].hex()}",
            ]
        )


def gunzip_at(data: bytes, offset: int) -> bytes:
    """Decompress a gzip stream starting at offset, ignoring trailing junk."""
    d = gzip.GzipFile(fileobj=io.BytesIO(data[offset:]))
    out = bytearray()
    while True:
        chunk = d.read(1 << 20)
        if not chunk:
            break
        out += chunk
    return bytes(out)


def extract_cpio(cpio_data: bytes, dest: Path) -> str:
    """Unpack a newc cpio archive using /usr/bin/cpio."""
    dest.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(
        ["/usr/bin/cpio", "-idmu", "--no-absolute-filenames"],
        input=cpio_data,
        cwd=dest,
        capture_output=True,
    )
    return p.stderr.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# kernel images
# --------------------------------------------------------------------------

IKCFG_ST = b"\x49\x4b\x43\x46\x47\x5f\x53\x54"  # "IKCFG_ST"
IKCFG_ED = b"\x49\x4b\x43\x46\x47\x5f\x45\x44"  # "IKCFG_ED"


def extract_ikconfig(kernel: bytes) -> bytes | None:
    """Return the .config embedded by CONFIG_IKCONFIG, if present."""
    i = kernel.find(IKCFG_ST)
    if i < 0:
        return None
    start = i + len(IKCFG_ST)
    j = kernel.find(IKCFG_ED, start)
    blob = kernel[start : j if j > 0 else len(kernel)]
    g = blob.find(b"\x1f\x8b\x08")
    if g < 0:
        return None
    try:
        return gunzip_at(blob, g)
    except (OSError, Exception):
        return None


def decompress_kernel(kernel: bytes) -> bytes | None:
    """Find and inflate the compressed vmlinux inside a zImage."""
    for off in find_all(kernel, b"\x1f\x8b\x08"):
        try:
            out = gunzip_at(kernel, off)
        except Exception:
            continue
        if len(out) > 512 * 1024:
            return out
    return None


# --------------------------------------------------------------------------
# flattened device tree
# --------------------------------------------------------------------------

FDT_MAGIC = b"\xd0\x0d\xfe\xed"

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def dtb_to_dts(blob: bytes) -> str:
    """Minimal flattened-devicetree printer (no dtc on this box)."""
    magic, totalsize, off_struct, off_strings, off_rsvmap, version = struct.unpack(
        ">6I", blob[:24]
    )
    if struct.pack(">I", magic) != FDT_MAGIC:
        raise ValueError("not a DTB")
    size_strings, size_struct = struct.unpack(">2I", blob[32:40])
    sblob = blob[off_strings : off_strings + size_strings]
    out: list[str] = []
    depth = 0
    p = off_struct
    end = off_struct + size_struct
    while p < end:
        (tok,) = struct.unpack(">I", blob[p : p + 4])
        p += 4
        if tok == FDT_BEGIN_NODE:
            z = blob.index(b"\0", p)
            name = blob[p:z].decode("latin1") or "/"
            p = (z + 4) & ~3
            out.append("\t" * depth + f"{name} {{")
            depth += 1
        elif tok == FDT_END_NODE:
            depth -= 1
            out.append("\t" * depth + "};")
        elif tok == FDT_PROP:
            plen, poff = struct.unpack(">2I", blob[p : p + 8])
            p += 8
            val = blob[p : p + plen]
            p = (p + plen + 3) & ~3
            z = sblob.index(b"\0", poff)
            pname = sblob[poff:z].decode("latin1")
            out.append("\t" * depth + f"{pname}{_fmt_prop(val)};")
        elif tok in (FDT_NOP,):
            continue
        elif tok == FDT_END:
            break
        else:
            out.append(f"/* unknown token {tok} at {p - 4} */")
            break
    return "\n".join(out)


def _fmt_prop(val: bytes) -> str:
    if not val:
        return ""
    if val[-1] == 0 and all(0x20 <= b < 0x7F or b == 0 for b in val[:-1]):
        parts = [s.decode("latin1") for s in val[:-1].split(b"\0")]
        return " = " + ", ".join(f'"{s}"' for s in parts)
    if len(val) % 4 == 0 and len(val) <= 64:
        cells = struct.unpack(f">{len(val) // 4}I", val)
        return " = <" + " ".join(f"0x{c:x}" for c in cells) + ">"
    return " = [" + " ".join(f"{b:02x}" for b in val) + "]"


def find_dtbs(data: bytes):
    """Yield (offset, size) for every plausible FDT in a blob."""
    for off in find_all(data, FDT_MAGIC):
        if off + 24 > len(data):
            continue
        (totalsize,) = struct.unpack(">I", data[off + 4 : off + 8])
        (version,) = struct.unpack(">I", data[off + 20 : off + 24])
        if 0x100 < totalsize < 4 << 20 and version in (16, 17):
            yield off, totalsize


if __name__ == "__main__":  # pragma: no cover
    path = Path(sys.argv[1])
    data = path.read_bytes()
    print(f"{path}: {len(data)} bytes")
    print(hexdump(data, limit=256))

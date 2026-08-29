"""Pure-python UCL (NRV2B/NRV2D/NRV2E) decompressors.

Amlogic packs the U-Boot proper inside `bootloader.img` with UCL -- the
`ucl decompress...` string in the TPL gives it away -- and there is no `ucl`
tool on this box. Transcribed from ucl-1.03 `src/n2b_d.c`, `n2d_d.c`,
`n2e_d.c` and `getbit.h` (GPL, Markus F.X.J. Oberhumer), 8-bit bit-buffer
variant, which is what the Amlogic packer uses.

Beware when re-deriving these: NRV2B's offset loop is a plain
`do { m_off = m_off*2 + bit } while (!bit)`, while NRV2D/NRV2E add the
`m_off = (m_off-1)*2 + bit` step. The three also differ in how the match
length is coded, and every variant copies `m_len + 1` bytes, not `m_len`.
"""

from __future__ import annotations


class Bits:
    """UCL getbit_8: `bb = bb & 0x7f ? bb*2 : src[ilen++]*2+1; (bb >> 8) & 1`."""

    __slots__ = ("src", "ilen", "bb")

    def __init__(self, src: bytes, ilen: int = 0):
        self.src = src
        self.ilen = ilen
        self.bb = 0

    def bit(self) -> int:
        bb = self.bb
        if bb & 0x7F:
            bb *= 2
        else:
            bb = self.src[self.ilen] * 2 + 1
            self.ilen += 1
        self.bb = bb
        return (bb >> 8) & 1

    def byte(self) -> int:
        b = self.src[self.ilen]
        self.ilen += 1
        return b


def _copy(out: bytearray, m_off: int, m_len: int) -> None:
    """The UCL match copy: emits m_len + 1 bytes."""
    if m_off > len(out):
        raise ValueError("lookbehind overrun")
    pos = len(out) - m_off
    for _ in range(m_len + 1):
        out.append(out[pos])
        pos += 1


def _tail(s: Bits, m_len: int) -> int:
    """The shared `if (m_len == 0) { exponential }` tail of NRV2B/NRV2D."""
    if m_len == 0:
        m_len = 1
        while True:
            m_len = m_len * 2 + s.bit()
            if s.bit():
                break
        m_len += 2
    return m_len


def nrv2b(src: bytes, ilen: int = 0, max_out: int = 8 << 20) -> bytes:
    s = Bits(src, ilen)
    out = bytearray()
    last_m_off = 1
    while True:
        while s.bit():
            out.append(s.byte())
        m_off = 1
        while True:
            m_off = m_off * 2 + s.bit()
            if m_off > 0xFFFFFF + 3:
                raise ValueError("lookbehind overrun")
            if s.bit():
                break
        if m_off == 2:
            m_off = last_m_off
        else:
            m_off = (m_off - 3) * 256 + s.byte()
            if m_off == 0xFFFFFFFF:
                break
            m_off += 1
            last_m_off = m_off
        m_len = s.bit()
        m_len = m_len * 2 + s.bit()
        m_len = _tail(s, m_len)
        m_len += m_off > 0xD00
        _copy(out, m_off, m_len)
        if len(out) > max_out:
            raise ValueError("output overrun")
    return bytes(out)


def _nrv2de_off(s: Bits, last_m_off: int):
    """The shared NRV2D/NRV2E offset decoder. Returns (m_off, m_len_seed, eof)."""
    m_off = 1
    while True:
        m_off = m_off * 2 + s.bit()
        if m_off > 0xFFFFFF + 3:
            raise ValueError("lookbehind overrun")
        if s.bit():
            break
        m_off = (m_off - 1) * 2 + s.bit()
    if m_off == 2:
        return last_m_off, s.bit(), False
    m_off = (m_off - 3) * 256 + s.byte()
    if m_off == 0xFFFFFFFF:
        return 0, 0, True
    m_len = (m_off ^ 0xFFFFFFFF) & 1
    m_off >>= 1
    m_off += 1
    return m_off, m_len, False


def nrv2d(src: bytes, ilen: int = 0, max_out: int = 8 << 20) -> bytes:
    s = Bits(src, ilen)
    out = bytearray()
    last_m_off = 1
    while True:
        while s.bit():
            out.append(s.byte())
        m_off, m_len, eof = _nrv2de_off(s, last_m_off)
        if eof:
            break
        last_m_off = m_off
        m_len = m_len * 2 + s.bit()
        m_len = _tail(s, m_len)
        m_len += m_off > 0x500
        _copy(out, m_off, m_len)
        if len(out) > max_out:
            raise ValueError("output overrun")
    return bytes(out)


def nrv2e(src: bytes, ilen: int = 0, max_out: int = 8 << 20) -> bytes:
    s = Bits(src, ilen)
    out = bytearray()
    last_m_off = 1
    while True:
        while s.bit():
            out.append(s.byte())
        m_off, m_len, eof = _nrv2de_off(s, last_m_off)
        if eof:
            break
        last_m_off = m_off
        if m_len:
            m_len = 1 + s.bit()
        elif s.bit():
            m_len = 3 + s.bit()
        else:
            m_len = 1
            while True:
                m_len = m_len * 2 + s.bit()
                if s.bit():
                    break
            m_len += 3
        m_len += m_off > 0x500
        _copy(out, m_off, m_len)
        if len(out) > max_out:
            raise ValueError("output overrun")
    return bytes(out)


ALGOS = {"nrv2b": nrv2b, "nrv2d": nrv2d, "nrv2e": nrv2e}


def try_all(src: bytes, ilen: int, min_out: int = 1024) -> dict[str, bytes]:
    """Return {variant: output} for every variant that decodes cleanly."""
    good = {}
    for name, fn in ALGOS.items():
        try:
            out = fn(src, ilen)
        except (ValueError, IndexError):
            continue
        if len(out) >= min_out:
            good[name] = out
    return good


# ---------------------------------------------------------------------------
# uclpack container, as used inside Amlogic's bootloader.img
# ---------------------------------------------------------------------------

UCL_MAGIC = b"\x00\xe9UCL\xff\x01\x1a"


def unpack_container(data: bytes, off: int, algo: str = "nrv2d") -> bytes:
    """Decompress a uclpack stream at `off`.

    Layout, derived by arithmetic from `bootloader.img` (each block header's
    compressed length lands exactly on the next header):

        +0x00  magic          8 bytes, "\\x00\\xe9UCL\\xff\\x01\\x1a"
        +0x09  version        BE32, 301 (= UCL 1.03)
        +0x0d  method         u8, 7 -- empirically NRV2D with an 8-bit bit buffer
        +0x0e  block_size     BE32, 0x40000
        +0x12  block[0..]     BE32 uncompressed_len, BE32 compressed_len, data

    A block whose compressed length equals its uncompressed length is stored,
    not compressed; a zero uncompressed length ends the stream.
    """
    if data[off : off + 8] != UCL_MAGIC:
        raise ValueError("no UCL magic")
    import struct

    (version,) = struct.unpack(">I", data[off + 9 : off + 13])
    method = data[off + 13]
    (block_size,) = struct.unpack(">I", data[off + 14 : off + 18])
    fn = ALGOS[algo]
    p = off + 18
    out = bytearray()
    while p + 8 <= len(data):
        ulen, clen = struct.unpack(">2I", data[p : p + 8])
        p += 8
        if ulen == 0:
            break
        if not (0 < clen <= block_size + 4096) or ulen > block_size:
            raise ValueError(f"bad block header at 0x{p - 8:x}: {ulen=} {clen=}")
        if clen == ulen:
            out += data[p : p + clen]
        else:
            blk = fn(data, p)
            if len(blk) != ulen:
                raise ValueError(f"block at 0x{p:x}: got {len(blk)}, want {ulen}")
            out += blk
        p += clen
    return bytes(out)

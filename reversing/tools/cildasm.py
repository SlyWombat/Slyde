"""A CIL (.NET IL) disassembler, for reading the frame's Xamarin app.

`system/app/Cadre.apk` is Xamarin.Android: the real logic is managed code in
`assemblies/CadreAndroid.dll`, not in `classes.dex`. There is no .NET toolchain
on this box (no mono, no ilspy, no ikdasm), so this walks the ECMA-335
metadata itself. `dnfile` supplies the metadata tables and heaps; method
bodies, the opcode stream and token resolution are done here.

    uv run --with dnfile reversing/tools/cildasm.py <assembly.dll> [Type.Method]

or, with the scratchpad venv that has dnfile installed:

    <venv>/bin/python reversing/tools/cildasm.py <assembly.dll>

Output is one line per instruction, with metadata tokens resolved to names and
`ldstr` resolved to the literal from the #US heap -- which is what makes this
readable enough to recover a network protocol from.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import dnfile

# ---------------------------------------------------------------------------
# opcode table (ECMA-335 III.1.2 / opcode.def)
# ---------------------------------------------------------------------------

NONE, VAR, I1, BR1, I4, I8, R4, R8, BR4, TOK, SWITCH, VAR2 = range(12)

OPERAND_SIZE = {NONE: 0, VAR: 2, VAR2: 2, I1: 1, BR1: 1, I4: 4, I8: 8,
                R4: 4, R8: 8, BR4: 4, TOK: 4, SWITCH: -1}

_ONE_BYTE = {
    0x00: ("nop", NONE), 0x01: ("break", NONE),
    0x02: ("ldarg.0", NONE), 0x03: ("ldarg.1", NONE), 0x04: ("ldarg.2", NONE),
    0x05: ("ldarg.3", NONE), 0x06: ("ldloc.0", NONE), 0x07: ("ldloc.1", NONE),
    0x08: ("ldloc.2", NONE), 0x09: ("ldloc.3", NONE), 0x0A: ("stloc.0", NONE),
    0x0B: ("stloc.1", NONE), 0x0C: ("stloc.2", NONE), 0x0D: ("stloc.3", NONE),
    0x0E: ("ldarg.s", I1), 0x0F: ("ldarga.s", I1), 0x10: ("starg.s", I1),
    0x11: ("ldloc.s", I1), 0x12: ("ldloca.s", I1), 0x13: ("stloc.s", I1),
    0x14: ("ldnull", NONE), 0x15: ("ldc.i4.m1", NONE),
    0x16: ("ldc.i4.0", NONE), 0x17: ("ldc.i4.1", NONE), 0x18: ("ldc.i4.2", NONE),
    0x19: ("ldc.i4.3", NONE), 0x1A: ("ldc.i4.4", NONE), 0x1B: ("ldc.i4.5", NONE),
    0x1C: ("ldc.i4.6", NONE), 0x1D: ("ldc.i4.7", NONE), 0x1E: ("ldc.i4.8", NONE),
    0x1F: ("ldc.i4.s", I1), 0x20: ("ldc.i4", I4), 0x21: ("ldc.i8", I8),
    0x22: ("ldc.r4", R4), 0x23: ("ldc.r8", R8), 0x25: ("dup", NONE),
    0x26: ("pop", NONE), 0x27: ("jmp", TOK), 0x28: ("call", TOK),
    0x29: ("calli", TOK), 0x2A: ("ret", NONE), 0x2B: ("br.s", BR1),
    0x2C: ("brfalse.s", BR1), 0x2D: ("brtrue.s", BR1), 0x2E: ("beq.s", BR1),
    0x2F: ("bge.s", BR1), 0x30: ("bgt.s", BR1), 0x31: ("ble.s", BR1),
    0x32: ("blt.s", BR1), 0x33: ("bne.un.s", BR1), 0x34: ("bge.un.s", BR1),
    0x35: ("bgt.un.s", BR1), 0x36: ("ble.un.s", BR1), 0x37: ("blt.un.s", BR1),
    0x38: ("br", BR4), 0x39: ("brfalse", BR4), 0x3A: ("brtrue", BR4),
    0x3B: ("beq", BR4), 0x3C: ("bge", BR4), 0x3D: ("bgt", BR4),
    0x3E: ("ble", BR4), 0x3F: ("blt", BR4), 0x40: ("bne.un", BR4),
    0x41: ("bge.un", BR4), 0x42: ("bgt.un", BR4), 0x43: ("ble.un", BR4),
    0x44: ("blt.un", BR4), 0x45: ("switch", SWITCH),
    0x46: ("ldind.i1", NONE), 0x47: ("ldind.u1", NONE), 0x48: ("ldind.i2", NONE),
    0x49: ("ldind.u2", NONE), 0x4A: ("ldind.i4", NONE), 0x4B: ("ldind.u4", NONE),
    0x4C: ("ldind.i8", NONE), 0x4D: ("ldind.i", NONE), 0x4E: ("ldind.r4", NONE),
    0x4F: ("ldind.r8", NONE), 0x50: ("ldind.ref", NONE),
    0x51: ("stind.ref", NONE), 0x52: ("stind.i1", NONE), 0x53: ("stind.i2", NONE),
    0x54: ("stind.i4", NONE), 0x55: ("stind.i8", NONE), 0x56: ("stind.r4", NONE),
    0x57: ("stind.r8", NONE), 0x58: ("add", NONE), 0x59: ("sub", NONE),
    0x5A: ("mul", NONE), 0x5B: ("div", NONE), 0x5C: ("div.un", NONE),
    0x5D: ("rem", NONE), 0x5E: ("rem.un", NONE), 0x5F: ("and", NONE),
    0x60: ("or", NONE), 0x61: ("xor", NONE), 0x62: ("shl", NONE),
    0x63: ("shr", NONE), 0x64: ("shr.un", NONE), 0x65: ("neg", NONE),
    0x66: ("not", NONE), 0x67: ("conv.i1", NONE), 0x68: ("conv.i2", NONE),
    0x69: ("conv.i4", NONE), 0x6A: ("conv.i8", NONE), 0x6B: ("conv.r4", NONE),
    0x6C: ("conv.r8", NONE), 0x6D: ("conv.u4", NONE), 0x6E: ("conv.u8", NONE),
    0x6F: ("callvirt", TOK), 0x70: ("cpobj", TOK), 0x71: ("ldobj", TOK),
    0x72: ("ldstr", TOK), 0x73: ("newobj", TOK), 0x74: ("castclass", TOK),
    0x75: ("isinst", TOK), 0x76: ("conv.r.un", NONE), 0x79: ("unbox", TOK),
    0x7A: ("throw", NONE), 0x7B: ("ldfld", TOK), 0x7C: ("ldflda", TOK),
    0x7D: ("stfld", TOK), 0x7E: ("ldsfld", TOK), 0x7F: ("ldsflda", TOK),
    0x80: ("stsfld", TOK), 0x81: ("stobj", TOK),
    0x82: ("conv.ovf.i1.un", NONE), 0x83: ("conv.ovf.i2.un", NONE),
    0x84: ("conv.ovf.i4.un", NONE), 0x85: ("conv.ovf.i8.un", NONE),
    0x86: ("conv.ovf.u1.un", NONE), 0x87: ("conv.ovf.u2.un", NONE),
    0x88: ("conv.ovf.u4.un", NONE), 0x89: ("conv.ovf.u8.un", NONE),
    0x8A: ("conv.ovf.i.un", NONE), 0x8B: ("conv.ovf.u.un", NONE),
    0x8C: ("box", TOK), 0x8D: ("newarr", TOK), 0x8E: ("ldlen", NONE),
    0x8F: ("ldelema", TOK), 0x90: ("ldelem.i1", NONE), 0x91: ("ldelem.u1", NONE),
    0x92: ("ldelem.i2", NONE), 0x93: ("ldelem.u2", NONE), 0x94: ("ldelem.i4", NONE),
    0x95: ("ldelem.u4", NONE), 0x96: ("ldelem.i8", NONE), 0x97: ("ldelem.i", NONE),
    0x98: ("ldelem.r4", NONE), 0x99: ("ldelem.r8", NONE), 0x9A: ("ldelem.ref", NONE),
    0x9B: ("stelem.i", NONE), 0x9C: ("stelem.i1", NONE), 0x9D: ("stelem.i2", NONE),
    0x9E: ("stelem.i4", NONE), 0x9F: ("stelem.i8", NONE), 0xA0: ("stelem.r4", NONE),
    0xA1: ("stelem.r8", NONE), 0xA2: ("stelem.ref", NONE), 0xA3: ("ldelem", TOK),
    0xA4: ("stelem", TOK), 0xA5: ("unbox.any", TOK),
    0xB3: ("conv.ovf.i1", NONE), 0xB4: ("conv.ovf.u1", NONE),
    0xB5: ("conv.ovf.i2", NONE), 0xB6: ("conv.ovf.u2", NONE),
    0xB7: ("conv.ovf.i4", NONE), 0xB8: ("conv.ovf.u4", NONE),
    0xB9: ("conv.ovf.i8", NONE), 0xBA: ("conv.ovf.u8", NONE),
    0xC2: ("refanyval", TOK), 0xC3: ("ckfinite", NONE), 0xC6: ("mkrefany", TOK),
    0xD0: ("ldtoken", TOK), 0xD1: ("conv.u2", NONE), 0xD2: ("conv.u1", NONE),
    0xD3: ("conv.i", NONE), 0xD4: ("conv.ovf.i", NONE), 0xD5: ("conv.ovf.u", NONE),
    0xD6: ("add.ovf", NONE), 0xD7: ("add.ovf.un", NONE), 0xD8: ("mul.ovf", NONE),
    0xD9: ("mul.ovf.un", NONE), 0xDA: ("sub.ovf", NONE), 0xDB: ("sub.ovf.un", NONE),
    0xDC: ("endfinally", NONE), 0xDD: ("leave", BR4), 0xDE: ("leave.s", BR1),
    0xDF: ("stind.i", NONE), 0xE0: ("conv.u", NONE),
}

_TWO_BYTE = {
    0x00: ("arglist", NONE), 0x01: ("ceq", NONE), 0x02: ("cgt", NONE),
    0x03: ("cgt.un", NONE), 0x04: ("clt", NONE), 0x05: ("clt.un", NONE),
    0x06: ("ldftn", TOK), 0x07: ("ldvirtftn", TOK), 0x09: ("ldarg", VAR),
    0x0A: ("ldarga", VAR), 0x0B: ("starg", VAR), 0x0C: ("ldloc", VAR),
    0x0D: ("ldloca", VAR), 0x0E: ("stloc", VAR), 0x0F: ("localloc", NONE),
    0x11: ("endfilter", NONE), 0x12: ("unaligned.", I1), 0x13: ("volatile.", NONE),
    0x14: ("tail.", NONE), 0x15: ("initobj", TOK), 0x16: ("constrained.", TOK),
    0x17: ("cpblk", NONE), 0x18: ("initblk", NONE), 0x19: ("no.", I1),
    0x1A: ("rethrow", NONE), 0x1C: ("sizeof", TOK), 0x1D: ("refanytype", NONE),
    0x1E: ("readonly.", NONE),
}

TABLE_NAMES = {
    0x01: "TypeRef", 0x02: "TypeDef", 0x04: "Field", 0x06: "MethodDef",
    0x0A: "MemberRef", 0x0B: "Constant", 0x1B: "TypeSpec", 0x2B: "MethodSpec",
    0x23: "AssemblyRef", 0x11: "StandAloneSig", 0x1A: "ModuleRef",
}


class Assembly:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.pe = dnfile.dnPE(str(path))
        self.md = self.pe.net.mdtables
        self._name_cache: dict[int, str] = {}

    # -- token resolution ---------------------------------------------------

    def _row(self, table_id: int, rid: int):
        name = TABLE_NAMES.get(table_id)
        t = getattr(self.md, name, None) if name else None
        if t is None or rid < 1 or rid > len(t.rows):
            return None
        return t.rows[rid - 1]

    def type_name(self, td) -> str:
        ns = str(getattr(td, "TypeNamespace", "") or "")
        nm = str(getattr(td, "TypeName", "") or "")
        return f"{ns}.{nm}" if ns else nm

    def resolve(self, token: int) -> str:
        table_id, rid = token >> 24, token & 0xFFFFFF
        if table_id == 0x70:  # #US heap
            return self.user_string(rid)
        row = self._row(table_id, rid)
        if row is None:
            return f"token(0x{token:08x})"
        if table_id == 0x06:  # MethodDef
            owner = self.method_owner.get(rid, "?")
            return f"{owner}::{row.Name}"
        if table_id == 0x0A:  # MemberRef
            cls = row.Class.row if getattr(row, "Class", None) else None
            parent = self.type_name(cls) if cls is not None else "?"
            return f"{parent}::{row.Name}"
        if table_id in (0x01, 0x02):
            return self.type_name(row)
        if table_id == 0x04:  # Field
            return f"field {row.Name}"
        return f"{TABLE_NAMES.get(table_id, hex(table_id))}[{rid}]"

    def user_string(self, offset: int) -> str:
        us = self.pe.net.user_strings
        try:
            item = us.get(offset)
        except Exception:
            return f"us(0x{offset:x})"
        if item is None:
            return f"us(0x{offset:x})"
        val = getattr(item, "value", item)
        if isinstance(val, bytes):
            val = val.decode("utf-16-le", "replace")
        return f'"{val}"'

    # -- structure ----------------------------------------------------------

    @property
    def types(self):
        return self.md.TypeDef.rows if self.md.TypeDef else []

    @property
    def method_owner(self) -> dict[int, str]:
        """MethodDef rid -> owning type name.

        dnfile already resolves each TypeDef's MethodList into the list of
        MDTableIndex entries that belong to it, so no run-splitting is needed.
        """
        if not hasattr(self, "_owner"):
            self._owner = {}
            for td in self.types:
                name = self.type_name(td)
                for idx in td.MethodList or []:
                    self._owner[idx.row_index] = name
        return self._owner

    def methods_of(self, td):
        for idx in td.MethodList or []:
            if idx.row is not None:
                yield idx.row_index, idx.row

    # -- method bodies ------------------------------------------------------

    def body(self, method) -> bytes | None:
        rva = method.Rva
        if not rva:
            return None
        head = self.pe.get_data(rva, 16)
        if not head:
            return None
        if (head[0] & 3) == 2:  # tiny
            size = head[0] >> 2
            return self.pe.get_data(rva + 1, size)[:size]
        flags_size, _maxstack, code_size, _lvt = struct.unpack("<HHII", head[:12])
        hdr = (flags_size >> 12) * 4
        return self.pe.get_data(rva + hdr, code_size)[:code_size]

    def disasm(self, code: bytes) -> list[tuple[int, str, str]]:
        out = []
        p = 0
        while p < len(code):
            start = p
            b = code[p]
            p += 1
            if b == 0xFE:
                name, kind = _TWO_BYTE.get(code[p], (f".unk_fe{code[p]:02x}", NONE))
                p += 1
            else:
                name, kind = _ONE_BYTE.get(b, (f".unk_{b:02x}", NONE))
            operand = ""
            if kind == SWITCH:
                (n,) = struct.unpack("<I", code[p : p + 4])
                p += 4
                targets = struct.unpack(f"<{n}i", code[p : p + 4 * n])
                p += 4 * n
                operand = ", ".join(f"IL_{p + t:04x}" for t in targets)
            else:
                size = OPERAND_SIZE[kind]
                raw = code[p : p + size]
                p += size
                if kind == TOK:
                    (tok,) = struct.unpack("<I", raw)
                    operand = self.resolve(tok)
                elif kind == BR1:
                    operand = f"IL_{p + struct.unpack('<b', raw)[0]:04x}"
                elif kind == BR4:
                    operand = f"IL_{p + struct.unpack('<i', raw)[0]:04x}"
                elif kind == I1:
                    operand = str(struct.unpack("<b", raw)[0])
                elif kind in (I4,):
                    operand = str(struct.unpack("<i", raw)[0])
                elif kind == I8:
                    operand = str(struct.unpack("<q", raw)[0])
                elif kind == R4:
                    operand = str(struct.unpack("<f", raw)[0])
                elif kind == R8:
                    operand = str(struct.unpack("<d", raw)[0])
                elif kind in (VAR, VAR2):
                    operand = str(struct.unpack("<H", raw)[0])
            out.append((start, name, operand))
        return out

    def dump_method(self, rid, method) -> str:
        owner = self.method_owner.get(rid, "?")
        lines = [f".method {owner}::{method.Name}  // MethodDef[{rid}] rva=0x{method.Rva:x}"]
        code = self.body(method)
        if code is None:
            lines.append("    // abstract or extern -- no body")
            return "\n".join(lines)
        for off, name, operand in self.disasm(code):
            lines.append(f"    IL_{off:04x}:  {name} {operand}".rstrip())
        return "\n".join(lines)

    def dump_all(self) -> str:
        chunks = []
        for td in self.types:
            tn = self.type_name(td)
            chunks.append(f"\n{'=' * 70}\n.class {tn}\n{'=' * 70}")
            for rid, m in self.methods_of(td):
                chunks.append(self.dump_method(rid, m))
        return "\n".join(chunks)


    # -- enums / literal constants -----------------------------------------

    def constants(self) -> dict[tuple[str, int], bytes]:
        """Constant-table blobs, keyed by (parent table, parent rid)."""
        out = {}
        for row in self.md.Constant.rows:
            par = row.Parent
            if par is None:
                continue
            raw = getattr(row.Value, "value", row.Value)
            if not isinstance(raw, (bytes, bytearray)):
                raw = getattr(row.Value, "raw_data", b"") or b""
            out[(par.table.name if par.table else "?", par.row_index)] = bytes(raw)
        return out

    def dump_enums(self) -> str:
        """Every type whose fields carry literal constants -- i.e. the enums.

        This is where the wire protocol lives: the three `Action` enums and the
        `Commands`/`COMMANDS` sets are the frame's complete command surface.
        """
        cons = self.constants()
        chunks = []
        for td in self.types:
            vals = []
            for idx in td.FieldList or []:
                if idx.row is None:
                    continue
                raw = cons.get(("Field", idx.row_index))
                if raw and len(raw) in (1, 2, 4, 8):
                    vals.append((str(idx.row.Name),
                                 int.from_bytes(raw, "little", signed=True)))
            if len(vals) > 1:
                chunks.append(f"== {self.type_name(td)} ==")
                chunks += [f"   {v:>10}  {n}" for n, v in vals]
                chunks.append("")
        return "\n".join(chunks)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    if argv[1] == "--enums":
        print(Assembly(argv[2]).dump_enums())
        return 0
    asm = Assembly(argv[1])
    if len(argv) > 2:
        want = argv[2].lower()
        for td in asm.types:
            for rid, m in asm.methods_of(td):
                full = f"{asm.type_name(td)}::{m.Name}".lower()
                if want in full:
                    print(asm.dump_method(rid, m))
                    print()
    else:
        print(asm.dump_all())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

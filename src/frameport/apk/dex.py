"""Minimal in-place dex edits: find one method's bytecode and turn a call in it into no-ops (same length), then fix the
header's SHA-1 signature and Adler-32 checksum. Nothing moves, so the rest of classes.dex stays byte-identical (no
smali round trip)."""
from __future__ import annotations

import hashlib
import struct
import zlib

INVOKE_VIRTUAL, INVOKE_VIRTUAL_RANGE = 0x6E, 0x74  # 35c / 3rc: 3 code units, method index in the second


def _uleb(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos
        shift += 7


class Dex:
    def __init__(self, data: bytes):
        if data[:4] != b"dex\n":
            raise ValueError("not a dex file")
        self.data = bytearray(data)
        h = struct.unpack_from("<8I", data, 56)  # string_ids size/off, type_ids, proto_ids, field_ids
        self.strings_n, self.strings_off, self.types_n, self.types_off = h[0], h[1], h[2], h[3]
        self.methods_n, self.methods_off, self.classes_n, self.classes_off = struct.unpack_from("<4I", data, 88)

    def string(self, idx: int) -> str:
        off = struct.unpack_from("<I", self.data, self.strings_off + 4 * idx)[0]
        _n, pos = _uleb(self.data, off)
        end = self.data.index(0, pos)
        return bytes(self.data[pos:end]).decode("utf-8", "replace")

    def type_name(self, idx: int) -> str:
        return self.string(struct.unpack_from("<I", self.data, self.types_off + 4 * idx)[0])

    def method(self, idx: int) -> tuple[str, str]:
        cls, _proto, name = struct.unpack_from("<HHI", self.data, self.methods_off + 8 * idx)
        return self.type_name(cls), self.string(name)

    def method_index(self, cls: str, name: str) -> list[int]:
        return [i for i in range(self.methods_n) if self.method(i) == (cls, name)]

    def code_items(self, cls: str, name: str) -> list[int]:
        """Offsets of the code items of the methods `name` in class `cls` (direct and virtual)."""
        out = []
        for c in range(self.classes_n):
            base = self.classes_off + 32 * c
            if self.type_name(struct.unpack_from("<I", self.data, base)[0]) != cls:
                continue
            class_data = struct.unpack_from("<I", self.data, base + 24)[0]
            if not class_data:
                continue
            pos = class_data
            sf, pos = _uleb(self.data, pos)
            inf, pos = _uleb(self.data, pos)
            dm, pos = _uleb(self.data, pos)
            vm, pos = _uleb(self.data, pos)
            for _ in range(sf + inf):
                _, pos = _uleb(self.data, pos)
                _, pos = _uleb(self.data, pos)
            for count in (dm, vm):
                idx = 0
                for _ in range(count):
                    diff, pos = _uleb(self.data, pos)
                    _, pos = _uleb(self.data, pos)
                    code, pos = _uleb(self.data, pos)
                    idx += diff
                    if code and self.method(idx)[1] == name:
                        out.append(code)
        return out

    def nop_calls(self, code_off: int, targets: set[int]) -> int:
        """Replace invoke-virtual(/range) calls of any method index in `targets` inside one code item with nops."""
        insns_n = struct.unpack_from("<I", self.data, code_off + 12)[0]
        start = code_off + 16
        units = struct.unpack_from(f"<{insns_n}H", self.data, start)
        done = 0
        for i in range(insns_n - 2):
            if units[i] & 0xFF in (INVOKE_VIRTUAL, INVOKE_VIRTUAL_RANGE) and units[i + 1] in targets:
                struct.pack_into("<3H", self.data, start + 2 * i, 0, 0, 0)
                done += 1
        return done

    def finish(self) -> bytes:
        self.data[12:32] = hashlib.sha1(bytes(self.data[32:])).digest()
        struct.pack_into("<I", self.data, 8, zlib.adler32(bytes(self.data[12:])) & 0xFFFFFFFF)
        return bytes(self.data)

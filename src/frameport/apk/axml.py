"""Minimal Android binary XML (AndroidManifest.xml) reader/editor.

Edits are surgical (string pool append, attribute retarget, element insert, boolean flip) so that the rest of the
file stays byte-identical, which keeps diffs against known-good builds meaningful.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator

RES_XML = 0x0003
RES_STRING_POOL = 0x0001
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
TYPE_STRING = 0x03
TYPE_INT_BOOLEAN = 0x12
ANDROID_NS = "http://schemas.android.com/apk/res/android"
LAUNCHER = "android.intent.category.LAUNCHER"
INFO = "android.intent.category.INFO"


@dataclass
class Attr:
    offset: int  # absolute offset of the attribute record
    ns: int
    name: int
    raw: int
    dtype: int
    value: int


@dataclass
class Element:
    offset: int  # chunk offset
    size: int
    name: str
    attrs: list[Attr]


class Axml:
    def __init__(self, data: bytes):
        self.data = bytearray(data)
        typ, hsz, _ = struct.unpack_from("<HHI", self.data, 0)
        if typ != RES_XML:
            raise ValueError("not a binary XML file")
        self.pool_off = hsz
        ptype, phsz, psize, self.count, self.styles, self.flags, self.sstart, self.ystart = struct.unpack_from(
            "<HHIIIIII", self.data, self.pool_off
        )
        if ptype != RES_STRING_POOL:
            raise ValueError("string pool not found")
        self.pool_hsz, self.pool_size = phsz, psize
        self.utf8 = bool(self.flags & 0x100)
        base = self.pool_off + phsz
        self.offsets = list(struct.unpack_from(f"<{self.count}I", self.data, base))
        self.style_offsets = list(struct.unpack_from(f"<{self.styles}I", self.data, base + 4 * self.count))
        self._strings: list[str] | None = None

    # ------------------------------------------------------------------ strings
    def string(self, i: int) -> str:
        p = self.pool_off + self.sstart + self.offsets[i]
        d = self.data
        if self.utf8:
            n = d[p]
            p += 2 if n & 0x80 else 1  # char count
            n = d[p]
            nb = ((n & 0x7F) << 8 | d[p + 1]) if n & 0x80 else n
            p += 2 if n & 0x80 else 1
            return bytes(d[p : p + nb]).decode("utf-8", "replace")
        n = struct.unpack_from("<H", d, p)[0]
        p += 2
        if n & 0x8000:
            n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", d, p)[0]
            p += 2
        return bytes(d[p : p + 2 * n]).decode("utf-16-le", "replace")

    def strings(self) -> list[str]:
        if self._strings is None:
            self._strings = [self.string(i) for i in range(self.count)]
        return self._strings

    def append_string(self, s: str) -> int:
        """Append s to the pool; returns its index. Rebuilds the pool chunk and the file size."""
        if self.utf8:
            b = s.encode("utf-8")
            if len(s) >= 0x80 or len(b) >= 0x80:
                raise ValueError("long UTF-8 strings are not supported")
            enc = bytes([len(s), len(b)]) + b + b"\0"
        else:
            enc = struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\0\0"
        d, po = self.data, self.pool_off
        str_end = po + (self.ystart if self.styles else self.pool_size)
        str_data = bytes(d[po + self.sstart : str_end])
        new_off = len(str_data)
        str_data += enc
        str_data += b"\0" * (-len(str_data) % 4)
        style_data = bytes(d[po + self.ystart : po + self.pool_size]) if self.styles else b""
        offsets = self.offsets + [new_off]
        sstart = self.pool_hsz + 4 * len(offsets) + 4 * self.styles
        ystart = sstart + len(str_data) if self.styles else 0
        body = (
            struct.pack(f"<{len(offsets)}I", *offsets)
            + struct.pack(f"<{self.styles}I", *self.style_offsets)
            + str_data
            + style_data
        )
        header = struct.pack(
            "<HHIIIIII", RES_STRING_POOL, self.pool_hsz, self.pool_hsz + len(body), len(offsets), self.styles,
            self.flags, sstart, ystart,
        )
        header += bytes(d[po + 28 : po + self.pool_hsz])  # any extra header bytes
        new = bytes(d[:po]) + header + body + bytes(d[po + self.pool_size :])
        self.__init__(new)
        struct.pack_into("<I", self.data, 4, len(self.data))
        return len(offsets) - 1

    def index(self, s: str) -> int | None:
        try:
            return self.strings().index(s)
        except ValueError:
            return None

    # ------------------------------------------------------------------ elements
    def elements(self) -> Iterator[Element]:
        names = self.strings()
        pos = struct.unpack_from("<H", self.data, 2)[0]
        while pos < len(self.data):
            typ, hsz, size = struct.unpack_from("<HHI", self.data, pos)
            if size == 0:
                break
            if typ == RES_XML_START_ELEMENT:
                ext = pos + hsz
                _, name, astart, asize, acount = struct.unpack_from("<IIHHH", self.data, ext)
                attrs = []
                for a in range(acount):
                    at = ext + astart + a * asize
                    ns, aname, raw = struct.unpack_from("<III", self.data, at)
                    dtype = self.data[at + 15]
                    value = struct.unpack_from("<I", self.data, at + 16)[0]
                    attrs.append(Attr(at, ns, aname, raw, dtype, value))
                yield Element(pos, size, names[name] if name < len(names) else "", attrs)
            pos += size

    def attr_str(self, el: Element, attr: str) -> str | None:
        names = self.strings()
        for a in el.attrs:
            if a.name < len(names) and names[a.name] == attr:
                if a.raw < len(names):
                    return names[a.raw]
                if a.dtype == TYPE_STRING and a.value < len(names):
                    return names[a.value]
        return None

    # ------------------------------------------------------------------ edits
    def retarget(self, element: str, old_index: int, new_index: int) -> int:
        """In every <element>, point string-valued attributes that reference old_index at new_index."""
        hits = 0
        for el in self.elements():
            if el.name != element:
                continue
            for a in el.attrs:
                if a.raw == old_index or (a.dtype == TYPE_STRING and a.value == old_index):
                    struct.pack_into("<I", self.data, a.offset + 8, new_index)
                    if a.dtype == TYPE_STRING:
                        struct.pack_into("<I", self.data, a.offset + 16, new_index)
                    hits += 1
        return hits

    def set_bool(self, element: str, attr: str, value: bool) -> int:
        names = self.strings()
        hits = 0
        for el in self.elements():
            if el.name != element:
                continue
            for a in el.attrs:
                if a.name < len(names) and names[a.name] == attr and a.dtype == TYPE_INT_BOOLEAN:
                    struct.pack_into("<I", self.data, a.offset + 16, 0xFFFFFFFF if value else 0)
                    hits += 1
        return hits

    def get_bool(self, element: str, attr: str) -> bool | None:
        names = self.strings()
        for el in self.elements():
            if el.name != element:
                continue
            for a in el.attrs:
                if a.name < len(names) and names[a.name] == attr and a.dtype == TYPE_INT_BOOLEAN:
                    return a.value != 0
        return None

    def bytes(self) -> bytes:
        return bytes(self.data)


# ---------------------------------------------------------------------- high-level queries/fixes
def used_and_declared_permissions(manifest: bytes) -> tuple[list[str], set[str]]:
    x = Axml(manifest)
    used, declared = [], set()
    for el in x.elements():
        if el.name in ("uses-permission", "permission"):
            name = x.attr_str(el, "name")
            if name:
                (used.append if el.name == "uses-permission" else declared.add)(name)
    return used, declared


def undeclared_meta_permissions(manifest: bytes) -> list[str]:
    used, declared = used_and_declared_permissions(manifest)
    return [p for p in used if p.startswith(("com.oculus.permission.", "horizonos.permission.")) and p not in declared]


def categories(manifest: bytes) -> set[str]:
    x = Axml(manifest)
    return {v for el in x.elements() if el.name == "category" for v in [x.attr_str(el, "name")] if v}


def fix_launcher(manifest: bytes) -> bytes | None:
    """Lepton only launches activities with category LAUNCHER: retarget category INFO to LAUNCHER."""
    x = Axml(manifest)
    names = x.strings()
    if INFO not in names or LAUNCHER in names:
        return None
    info = names.index(INFO)
    launcher = x.append_string(LAUNCHER)
    return x.bytes() if x.retarget("category", info, launcher) else None


def set_bool_attr(manifest: bytes, element: str, attr: str, value: bool) -> bytes | None:
    x = Axml(manifest)
    return x.bytes() if x.set_bool(element, attr, value) else None


def define_meta_permissions(manifest: bytes) -> tuple[bytes, list[str]] | None:
    """Declare Meta-only permissions (com.oculus.*, horizonos.*) the app uses. A permission the app declares itself
    with the default 'normal' protection level is granted at install, so checks like "allow spatial data" pass."""
    x = Axml(manifest)
    wanted = undeclared_meta_permissions(manifest)
    if not wanted or x.index("name") is None:
        return None
    android_ns = x.index(ANDROID_NS)
    if android_ns is None:
        return None
    perm_el = x.index("permission")
    if perm_el is None:
        perm_el = x.append_string("permission")
    names = x.strings()
    name_attr = names.index("name")
    chunks = b""
    for p in wanted:
        value = names.index(p)
        start = struct.pack("<HHIII", RES_XML_START_ELEMENT, 16, 56, 0, 0xFFFFFFFF)
        start += struct.pack("<IIHHHHHH", 0xFFFFFFFF, perm_el, 20, 20, 1, 0, 0, 0)
        start += struct.pack("<IIIHBBI", android_ns, name_attr, value, 8, 0, TYPE_STRING, value)
        end = struct.pack("<HHIII", RES_XML_END_ELEMENT, 16, 24, 0, 0xFFFFFFFF) + struct.pack("<II", 0xFFFFFFFF, perm_el)
        chunks += start + end
    manifest_el = next(el for el in x.elements() if el.name == "manifest")
    insert_at = manifest_el.offset + manifest_el.size
    data = x.data[:insert_at] + chunks + x.data[insert_at:]
    struct.pack_into("<I", data, 4, len(data))
    return bytes(data), wanted

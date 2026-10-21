"""A string resource's value, read by name from an APK's ``resources.arsc``.

Minimal reader of the binary resource table: the global string pool, the key
pool of each package, and the ``string`` Type entries. Any malformed input
raises nothing and yields ``""`` (callers fall back to scanning).
"""

from __future__ import annotations

import struct

_POOL, _PACKAGE, _TYPE = 0x0001, 0x0200, 0x0201
_UTF8_FLAG = 0x100
_TYPE_STRING = 0x03
_NO_ENTRY = 0xFFFFFFFF


def _u16(blob: bytes, at: int) -> int:
    return int(struct.unpack_from("<H", blob, at)[0])


def _u32(blob: bytes, at: int) -> int:
    return int(struct.unpack_from("<I", blob, at)[0])


def _len8(blob: bytes, at: int) -> tuple[int, int]:
    """A UTF-8 pool length (1 or 2 bytes) and the bytes it took."""
    first = blob[at]
    if first & 0x80:
        return ((first & 0x7F) << 8) | blob[at + 1], 2
    return first, 1


def _len16(blob: bytes, at: int) -> tuple[int, int]:
    """A UTF-16 pool length (1 or 2 units) and the bytes it took."""
    first = _u16(blob, at)
    if first & 0x8000:
        return ((first & 0x7FFF) << 16) | _u16(blob, at + 2), 4
    return first, 2


def pool_string(blob: bytes, pool: int, index: int) -> str:
    """String *index* of the pool chunk starting at *pool*."""
    flags, base = _u32(blob, pool + 16), pool + _u32(blob, pool + 20)
    at = base + _u32(blob, pool + _u16(blob, pool + 2) + 4 * index)
    if flags & _UTF8_FLAG:
        _, took = _len8(blob, at)
        size, took2 = _len8(blob, at + took)
        start = at + took + took2
        return blob[start : start + size].decode("utf-8", "replace")
    size, took = _len16(blob, at)
    return blob[at + took : at + took + 2 * size].decode("utf-16-le", "replace")


def _pool_find(blob: bytes, pool: int, name: str) -> int:
    """The index of *name* in the pool at *pool*, or -1."""
    for index in range(_u32(blob, pool + 8)):
        if pool_string(blob, pool, index) == name:
            return index
    return -1


def _entry_value(blob: bytes, entry: int, key: int) -> int:
    """The global-pool index of a string entry whose key is *key*, or -1."""
    if _u32(blob, entry + 4) != key or _u16(blob, entry) < 8:
        return -1
    if blob[entry + 8 + 3] != _TYPE_STRING:
        return -1
    return _u32(blob, entry + 8 + 4)


def _type_values(blob: bytes, chunk: int, key: int) -> list[int]:
    """Global-pool indexes of the entries of a Type chunk keyed *key*."""
    found = []
    count, start = _u32(blob, chunk + 12), chunk + _u32(blob, chunk + 16)
    table = chunk + _u16(blob, chunk + 2)
    for i in range(count):
        offset = _u32(blob, table + 4 * i)
        if offset != _NO_ENTRY:
            found.append(_entry_value(blob, start + offset, key))
    return [v for v in found if v >= 0]


def _package_values(blob: bytes, pkg: int, name: str) -> list[int]:
    """Global-pool indexes of every string entry named *name* in a package."""
    key = _pool_find(blob, pkg + _u32(blob, pkg + 276), name)
    end, at = pkg + _u32(blob, pkg + 4), pkg + _u16(blob, pkg + 2)
    values: list[int] = []
    while key >= 0 and at + 8 <= end:
        if _u16(blob, at) == _TYPE:
            values += _type_values(blob, at, key)
        at += max(_u32(blob, at + 4), 8)
    return values


def string_resource(blob: bytes, name: str) -> str:
    """The first value of the string resource *name*, else ``""``."""
    try:
        pool = _u16(blob, 2)
        if _u16(blob, pool) != _POOL:
            return ""
        at = pool + _u32(blob, pool + 4)
        values = _package_values_all(blob, at, name)
        return pool_string(blob, pool, values[0]) if values else ""
    except (struct.error, IndexError, ValueError):
        return ""


def _package_values_all(blob: bytes, at: int, name: str) -> list[int]:
    """Values of *name* across the packages that follow the global pool."""
    values: list[int] = []
    while at + 8 <= len(blob):
        if _u16(blob, at) == _PACKAGE:
            values += _package_values(blob, at, name)
        at += max(_u32(blob, at + 4), 8)
    return values

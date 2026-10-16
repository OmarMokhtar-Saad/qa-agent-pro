"""A downsampled raw frame and a tolerant compare (Maestro-style "did the screen move").

``adb exec-out screencap`` (no ``-p``) answers a 12- or 16-byte header (width,
height, format[, colorspace], little endian) and then 4 bytes per pixel. The
frame is cropped (status bar off: its clock moves on its own) and sampled on a
coarse grid, so two back-to-back frames compare in about a millisecond. They are
the same screen when at most :data:`TOLERANCE` of the sampled pixels differ:
a blinking text caret or a one-pixel animation never counts as a move. A flat frame
(a FLAG_SECURE window is uniform black) carries no signal and is never trusted.
Every failure answers ``None``; the caller then falls back to the md5 probe.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
from typing import NamedTuple

from tools.mobile import adb_run
from tools.mobile.adb_host import host

#: A frame is static when at most this share of sampled pixels differ. On a
#: 1080x2400 screen the grid has ~19k samples, so 0.03% is 5: a 3x50 px text
#: caret (~3 samples) stays static, a 60x60 badge (~28) or a 136x84 Switch
#: (~85) is a move. 0.5% (Maestro) hid those and the model re-toggled them.
TOLERANCE = 0.0003
#: Top share of the rows left out of the compare (the status bar).
STATUS_BAR_SHARE = 0.04
ROW_STEP = 16
COL_STEP = 8
HEADERS = (16, 12)
MAX_RAW_BYTES = 64 * 1024 * 1024
#: At most this many distinct sampled values and the frame is flat (one colour).
FLAT_MAX_VALUES = 1


class Frame(NamedTuple):
    """The sampled pixels (4-byte words), their md5 and whether the frame is flat."""

    words: bytes
    digest: str
    flat: bool


def _layout(raw: bytes) -> tuple[int, int, int] | None:
    """``(width, height, header_len)`` when *raw* is a whole raw frame, else ``None``."""
    for header in HEADERS:
        if len(raw) < header:
            continue
        width, height = struct.unpack_from("<II", raw, 0)
        if width and height and len(raw) == header + width * height * 4:
            return width, height, header
    return None


def sample(raw: bytes) -> Frame | None:
    """The cropped, downsampled frame in *raw*, or ``None`` when it is no raw frame."""
    found = _layout(raw)
    if found is None:
        return None
    width, height, header = found
    pixels = memoryview(raw)[header:].cast("I")
    top = int(height * STATUS_BAR_SHARE)
    rows = [
        pixels[y * width : (y + 1) * width : COL_STEP].tobytes()
        for y in range(top, height, ROW_STEP)
    ]
    words = b"".join(rows)
    flat = len(set(memoryview(words).cast("I"))) <= FLAT_MAX_VALUES
    return Frame(words, hashlib.md5(words).hexdigest(), flat)


def static(before: bytes, after: bytes) -> bool:
    """True when the two sampled frames differ in at most :data:`TOLERANCE` of pixels."""
    if not before or len(before) != len(after):
        return False
    one, two = memoryview(before).cast("I"), memoryview(after).cast("I")
    differing = sum(1 for a, b in zip(one, two, strict=True) if a != b)
    return differing <= len(one) * TOLERANCE


async def fetch(serial: str, budget: float) -> Frame | None:
    """One sampled frame from the device inside *budget* seconds, else ``None``."""
    if budget <= 0 or not host()._valid_device_id(serial):
        return None
    cmd = [host().resolve_adb(), "-s", str(serial), "exec-out", "screencap"]
    try:
        rc, out, _err = await adb_run.run_argv(cmd, budget)
    except (OSError, asyncio.TimeoutError, ValueError):
        return None
    if rc != 0 or len(out) > MAX_RAW_BYTES:
        return None
    return sample(out)

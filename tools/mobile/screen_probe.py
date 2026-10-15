"""A cheap "did the screen move" signal: focused window + on-device screencap md5.

One ``adb shell`` call (about 0.4 s on a phone where a uiautomator dump costs
2.3-3.3 s) names the focused ``package/Activity`` and the md5 of the live frame.
Two equal readings mean nothing visibly moved; a different one means a real dump
is worth paying for. False "moved" (a clock, a blinking cursor) only costs the
dump taken today. Every failure path answers "unknown", never "unchanged".

FLAG_SECURE: a protected window yields a black, constant frame, so its hash
never moves. :func:`blank` counts the frame's bytes that are neither 0x00 nor
0xFF (an all-black RGBA frame has only its few header bytes); a blank frame
(or a failed check) is never trusted as a baseline, so the caller keeps
dumping exactly as before. The check is cached per frame digest, not per
window: a window that sets FLAG_SECURE later shows a new (black) digest, so it
is checked again rather than trusted on an old verdict.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re

from tools.mobile import adb, adb_facts

READ_CMD = "dumpsys window | grep mCurrentFocus; echo @@; screencap | md5sum"
BLANK_CMD = "screencap | tr -d '\\000\\377' | wc -c"
#: A frame with at most this many "colour" bytes is black or one flat colour.
BLANK_MAX_BYTES = 64
#: Frame digests whose blank verdict is remembered (oldest dropped first).
MAX_BLANK_VERDICTS = 256
_BLANK: dict = {}
_HEX32 = re.compile(r"\b[0-9a-f]{32}\b")


@dataclasses.dataclass(frozen=True)
class Reading:
    """The focused window and the frame hash taken together."""

    focus: str
    digest: str


def same(before: Reading, now: Reading) -> bool:
    """True when the frame is identical and the focus is not a known different one."""
    if not before.digest or before.digest != now.digest:
        return False
    return not (before.focus and now.focus and before.focus != now.focus)


def parse(out: str) -> Reading | None:
    """The reading in a :data:`READ_CMD` answer, or ``None`` when it has no hash."""
    head, _sep, tail = str(out or "").partition("@@")
    found = _HEX32.search(tail)
    if not found:
        return None
    return Reading(adb_facts.focused_component(head), found.group(0))


async def _shell(serial: str, command: str, budget: float) -> str | None:
    """The command's stdout, or ``None`` on any failure inside *budget* seconds."""
    if budget <= 0:
        return None
    try:
        result = await asyncio.wait_for(
            adb.shell(serial, [command], timeout=int(budget) + 1), budget
        )
    except (TimeoutError, OSError, ValueError, KeyError):
        return None
    if not isinstance(result, dict) or result.get("error"):
        return None
    return str((result.get("content") or {}).get("out") or "")


async def read(serial: str, budget: float) -> Reading | None:
    """One probe, or ``None`` when unknown (adb error, unsupported, over budget)."""
    out = await _shell(serial, READ_CMD, budget)
    return None if out is None else parse(out)


async def blank(serial: str, digest: str, budget: float) -> bool:
    """True when the frame is flat black/one colour or cannot be told (distrust)."""
    key = (str(serial), digest)
    if key not in _BLANK:
        out = await _shell(serial, BLANK_CMD, budget)
        if out is None or not out.strip().isdigit():
            return True
        while len(_BLANK) >= MAX_BLANK_VERDICTS:
            del _BLANK[next(iter(_BLANK))]
        _BLANK[key] = int(out.strip()) <= BLANK_MAX_BYTES
    return _BLANK[key]


async def baseline(serial: str, budget: float) -> Reading | None:
    """A reading fit to compare later ones against; ``None`` when not trustworthy."""
    reading = await read(serial, budget)
    if reading is None or await blank(serial, reading.digest, budget):
        return None
    return reading

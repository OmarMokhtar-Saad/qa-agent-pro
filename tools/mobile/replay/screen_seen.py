"""Per-device ring of the last screens read, for the impossible-wait guard.

Entries older than :data:`MAX_AGE_S`, or read before the last actuation, are not
returned: a screen that existed before a tap says nothing about a wait after it."""

from __future__ import annotations

import collections
import time

from tools.mobile import dump_reuse, wait_guard

MAX_AGE_S = 60.0
MAX_SERIALS = 16

_RING: dict = {}


def _words(row: object) -> list:
    """The text and description of one element, whitespace-normalised."""
    if not isinstance(row, dict):
        return []
    fields = (" ".join(str(row.get(f) or "").split()) for f in ("text", "desc"))
    return [value for value in fields if value]


def screen_text(screen: object) -> str:
    """The visible text of one observed screen, in element order."""
    body = screen.get("content") if isinstance(screen, dict) else None
    rows = body.get("elements") if isinstance(body, dict) else None
    return "\n".join(word for row in rows or [] for word in _words(row))


def note(serial: str, screen: object) -> None:
    """Remember one fresh read of *serial*."""
    key = str(serial or "")
    ring = _RING.pop(key, None) or collections.deque(maxlen=wait_guard.SEEN)
    ring.append((dump_reuse.token(key), time.monotonic(), screen_text(screen)))
    _RING[key] = ring
    while len(_RING) > MAX_SERIALS:
        del _RING[next(iter(_RING))]


def reads(serial: str) -> list:
    """The latest reads (text + time), oldest first, since the last actuation."""
    key = str(serial or "")
    now, gen = time.monotonic(), dump_reuse.token(key)
    held = _RING.get(key, ())
    return [
        wait_guard.Read(t, at)
        for g, at, t in held
        if g == gen and now - at <= MAX_AGE_S
    ]


def recent(serial: str) -> list:
    """The texts of the latest reads, oldest first, since the last actuation."""
    return [r.text for r in reads(serial)]


def reset() -> None:
    """Forget every read (tests)."""
    _RING.clear()

"""The step's own screen probe, kept briefly so the same step probes once.

A step used to probe the screen up to three times (the anchor, then each wait
that wanted a baseline), about 0.7 s apiece. A trusted reading taken a moment
ago, with nothing actuated since (same generation), is as good as a new one, so
it is handed back instead of paying for another ``screencap``.
"""

from __future__ import annotations

import time

from tools.mobile import dump_reuse

#: Seconds a kept reading may be handed back; a probe is ~0.7 s, a step far longer.
REUSE_S = 1.5
_KEPT: dict = {}


def keep(serial: str, reading: object) -> None:
    """Remember a trusted (non-blank) *reading* with its generation and time."""
    key = str(serial or "")
    if reading is not None:
        _KEPT[key] = (dump_reuse.token(key), time.monotonic(), reading)


def fresh(serial: str) -> object | None:
    """The kept reading when nothing actuated since and it is younger than REUSE_S."""
    key = str(serial or "")
    gen, taken, reading = _KEPT.get(key) or (None, 0.0, None)
    if reading is None or gen != dump_reuse.token(key):
        return None
    return reading if time.monotonic() - taken <= REUSE_S else None


def drop(serial: str) -> None:
    """Forget the kept reading (a new dump has begun)."""
    _KEPT.pop(str(serial or ""), None)

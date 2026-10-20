"""Impossible-wait guard: refuse a wait that the recent screens prove cannot end.

Pure and app-neutral. ``screens`` is the last reads of the run, oldest first, each a
``Read`` (visible text + monotonic read time). Call it before blocking on a wait."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import NamedTuple

SEEN = 3  # screens that must agree before a wait is called impossible
MIN_SPAN_S = 5.0  # ...and the first and last of them must be this far apart
_DIGITS = re.compile(r"\d+")


class Read(NamedTuple):
    """One observed screen: its visible text and when it was read."""

    text: str
    at: float


def _recent(screens: Sequence[Read]) -> list[Read]:
    return list(screens)[-SEEN:] if len(screens) >= SEEN else []


def _span(reads: list[Read]) -> float:
    return reads[-1].at - reads[0].at if reads else 0.0


def _stays(target: str, reads: list[Read]) -> bool:
    key = target.strip().lower()
    unchanged = len({r.text for r in reads}) == 1  # nothing moved between reads
    return bool(key) and unchanged and key in reads[0].text.lower()


def _ticks(reads: list[Read]) -> bool:
    """Same text apart from digits, but never the same twice: a countdown or clock."""
    screens = [r.text for r in reads]
    if not screens or len(set(screens)) != len(screens):
        return False
    shapes = {_DIGITS.sub("#", s) for s in screens}
    return len(shapes) == 1 and "#" in next(iter(shapes))


def _unchanged_reason(target: str, reads: list[Read]) -> str:
    return (
        "%r was on the last %d screens unchanged for %.1f s; wait longer "
        "(raise max_s) or stop and tell the user" % (target, SEEN, _span(reads))
    )


def impossible_wait(op: str, target: str, screens: Sequence[Read]) -> str:
    """Why this wait may never finish, or "" when it may. op: wait_until_gone|idle."""
    recent = _recent(screens)
    gone = op == "wait_until_gone" and _span(recent) >= MIN_SPAN_S
    if gone and _stays(target, recent):
        return _unchanged_reason(target, recent)
    if op == "wait_until_idle" and _ticks(recent):
        return "the screen changes on every read (a counter); it will never be idle"
    return ""

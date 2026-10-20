"""Impossible-wait guard: refuse a wait that the recent screens prove cannot end.

Pure and app-neutral. ``screens`` is the visible text of the last screens of the run,
oldest first (one string per screen). Call it before blocking on a wait."""

from __future__ import annotations

import re
from collections.abc import Sequence

SEEN = 3  # screens that must agree before a wait is called impossible
_DIGITS = re.compile(r"\d+")


def _recent(screens: Sequence[str]) -> list[str]:
    return [str(s) for s in screens][-SEEN:] if len(screens) >= SEEN else []


def _stays(target: str, screens: list[str]) -> bool:
    key = target.strip().lower()
    unchanged = len(set(screens)) == 1  # nothing moved between reads
    return bool(key) and unchanged and key in screens[0].lower()


def _ticks(screens: list[str]) -> bool:
    """Same text apart from digits, but never the same twice: a countdown or clock."""
    if not screens or len(set(screens)) != len(screens):
        return False
    shapes = {_DIGITS.sub("#", s) for s in screens}
    return len(shapes) == 1 and "#" in next(iter(shapes))


def impossible_wait(op: str, target: str, screens: Sequence[str]) -> str:
    """Why this wait can never finish, or "" when it may. op: wait_until_gone|idle."""
    recent = _recent(screens)
    if op == "wait_until_gone" and _stays(target, recent):
        return "%r was on the last %d screens unchanged; it will not go away" % (
            target,
            SEEN,
        )
    if op == "wait_until_idle" and _ticks(recent):
        return "the screen changes on every read (a counter); it will never be idle"
    return ""

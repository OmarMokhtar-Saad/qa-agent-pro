"""The uiautomator read a stale verdict already paid for, handed to the dump that follows.

``a11y_fresh`` proves a repeated tree with one uiautomator dump. When that dump
contradicts the a11y tree the provider refuses, and ``adb.uiautomator_dump`` used to
read the same screen a SECOND time (measured: a 4.7 s step-1 dump, two uiautomator
reads back to back). The first read IS the real screen, so it is kept here for that
one call. It is served only if no device command that may move the screen ran since
the read began (the :mod:`tools.mobile.dump_reuse` generation) -- however long ago
that was: the generation, not an age, is the proof. It is spent once. Anything else
falls through to a fresh dump, so a screen read before an action is never treated
as current.
"""

from __future__ import annotations

from tools.mobile import dump_reuse

#: serial -> (generation the read began under, the dump envelope).
_KEPT: dict[str, tuple[int, dict]] = {}


def keep(serial: str, under: int, read: dict) -> None:
    """Keep *read* (a clean uiautomator envelope) begun under generation *under*."""
    if read.get("error") or not read.get("content"):
        return
    _KEPT[str(serial)] = (under, read)


def take(serial: str) -> dict | None:
    """The kept read when no actuation ran since it began, once."""
    kept = _KEPT.pop(str(serial), None)
    if kept is None:
        return None
    under, read = kept
    return read if under == dump_reuse.token(str(serial)) else None


def reset() -> None:
    """Forget every kept read. For tests."""
    _KEPT.clear()

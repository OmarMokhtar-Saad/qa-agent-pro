"""Replay waits: a change the wait missed, found by one fresh read after it ended.

A wait for the screen to change that ran to its limit says "did not change"
only when a fresh read after the wait agrees with the screen it started on. The
change can land just after the cap (measured: ``mrun-20261018-132444-62abaf``,
the screen key moved after every "did not change in 3 s"); a sentence that
ignored it told the model the screen was unchanged when it was not.
"""

from __future__ import annotations

from tools.mobile.screen_key import screen_key

CHANGED, SAME, UNKNOWN = "changed", "same", "unknown"


def _readable(screen: object) -> bool:
    """True for a perception screen that carries its elements."""
    return isinstance(screen, dict) and isinstance(screen.get("elements"), list)


def late_verdict(before: dict, after: dict) -> str:
    """``changed``, ``same`` or ``unknown`` (a screen was unreadable): *after* vs *before*."""
    if not (_readable(before) and _readable(after)):
        return UNKNOWN
    if any(before.get(k) != after.get(k) for k in ("package", "activity")):
        return CHANGED
    a, b = screen_key(before), screen_key(after)
    moved = a.anchors != b.anchors if a.degraded or b.degraded else a.key != b.key
    return CHANGED if moved else SAME


def late_detail(verdict: str, secs: str) -> str:
    """The trace sentence for a wait that ran *secs* and was then read afresh."""
    if verdict == CHANGED:
        return "the screen changed (after the " + secs + " wait ended)"
    return (
        "the screen could not be read after the "
        + secs
        + " wait: whether it changed is unknown"
    )

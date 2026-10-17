"""Replay waits: the one mapping from any wait op onto a condition wait, and its words."""

from __future__ import annotations

from tools.mobile import actions as actions_mod
from tools.mobile.replay.context_consts import LEGACY_WAIT_MAX_MS
from tools.mobile.replay.waits_types import WaitSpec


def _spec_parts(action: object, items: object) -> tuple[str, str, int]:
    """``(kind, text, ms)`` for any wait op: the parts of a :class:`WaitSpec`."""
    op = str(getattr(action, "op", "") or "")
    if op == "wait":
        until_text = str(getattr(action, "until_text", "") or "").strip()
        ms = int(getattr(action, "ms", 0) or 0)
        until_rid = str(getattr(action, "until_rid", "") or "").strip()
        if until_rid:
            return "element", until_rid, ms or actions_mod.bare_wait_ms_each(items)
        if until_text:
            return "text", until_text, ms or actions_mod.bare_wait_ms_each(items)
        return "changed", "", min(ms, LEGACY_WAIT_MAX_MS)
    ms = int(float(getattr(action, "max_s", 0) or 0) * 1000)
    kind = op[len("wait_until_") :]
    return kind, str(getattr(action, "text", "") or "").strip(), ms


def _wait_spec(action: object, items: object) -> WaitSpec:
    """``(kind, text, ms)`` for any wait op: the one mapping from the legacy
    ``wait`` onto the condition waits (S17, owner decision).

    ``wait until_text`` is ``wait_until_text``, with its split bare share of
    the total when it set no ms; ``wait ms`` is ``wait_until_changed`` bounded
    by that ms and clipped to ``LEGACY_WAIT_MAX_MS`` -- still parsed, never
    refused, and never a fixed sleep.
    """
    return WaitSpec(*_spec_parts(action, items))


def _wait_words(kind: str, text: str) -> tuple[str, str, str]:
    """``(met, timed_out, doing)`` -- the trace sentences for one wait kind."""
    quoted = repr(text[:120])
    if kind == "gone":
        return (
            quoted + " is gone",
            "timed out waiting for " + quoted + " to go",
            "waiting for " + quoted + " to go",
        )
    if kind == "idle":
        return (
            "the screen settled",
            "timed out waiting for the screen to settle",
            "waiting for the screen to settle",
        )
    return (
        "found " + quoted,
        "timed out waiting for " + quoted,
        "waiting for " + quoted,
    )


def _secs(ms: int) -> str:
    """``8000`` -> ``"8s"``, ``2500`` -> ``"2.5s"``."""
    return ("%g" % (ms / 1000.0)) + "s"


def _condition_words(action: object, kind: str, wait_text: str) -> tuple[str, str, str]:
    """The ``(met, missed, label)`` sentences for a wait condition."""
    if kind != "element":
        return _wait_words(kind, wait_text)
    gone = bool(getattr(action, "until_gone", False))
    label = "element " + repr(wait_text[:120]) + (" gone" if gone else " present")
    return "found " + label, "timed out waiting for " + label, label


# A wait that ended before this share of its limit was cut short, not run out.
EARLY_FRACTION = 0.9


def ended_early(elapsed_ms: int, wait_ms: int) -> bool:
    """True when a wait of *wait_ms* ended in under ``EARLY_FRACTION`` of it."""
    return wait_ms > 0 and elapsed_ms < wait_ms * EARLY_FRACTION


def stamp_early(entry: dict, elapsed_ms: int, wait_ms: int) -> None:
    """Mark *entry* ``wait_early`` when its wait ended early (LEARN reads it)."""
    if ended_early(elapsed_ms, wait_ms):
        entry["wait_early"] = True


# The "did not change" sentence names the time the wait REALLY took. A wait cut
# short (a dump that kept failing, the focus probe giving up) came back well
# inside its limit, and "did not change in 15s" for a 1.1 s wait is the
# true-shaped sentence that misleads the model about how long the screen was
# watched. A wait that ran to its limit keeps the plain wording.
def _unchanged_detail(wait_ms: int, elapsed_ms: int) -> str:
    """What an unchanged ``wait_until_changed`` DID: the real time, and an early end."""
    if not ended_early(elapsed_ms, wait_ms):
        return "the screen did not change in " + _secs(wait_ms)
    return (
        "the screen did not change in "
        + _secs(elapsed_ms)
        + " (the wait ended early, before its "
        + _secs(wait_ms)
        + " limit: the screen could not be read or settled)"
    )

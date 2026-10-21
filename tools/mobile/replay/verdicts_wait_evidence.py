"""``assert screen_changed`` after wait-only steps: the wait's own outcome is the evidence.

An overlay leaving does not change the screen id, so an id comparison is false
by construction after a wait. The wait step already recorded how it ended in the
trace (``ok`` or ``wait_timeout`` with its detail); that is the verdict.
"""

from __future__ import annotations

from tools.mobile.action_consts import WAIT_OPS

#: Waits whose ``ok`` means the screen CHANGED. ``wait_until_idle`` only means settled.
EVIDENCE_OPS = ("wait_until_gone", "wait_until_changed", "wait_until_text")
NUDGE = "screen_changed is weak after a wait; use assert text_absent"


def _op(entry: dict) -> str:
    action = entry.get("action")
    return str((action or {}).get("op") or "") if isinstance(action, dict) else ""


def _acts(trace: list[dict]) -> list[dict]:
    return [e for e in trace if isinstance(e, dict) and _op(e) != "assert"]


def _nudged(trace: list[dict]) -> bool:
    return any(
        NUDGE in str(e.get("detail") or "") for e in trace if isinstance(e, dict)
    )


def wait_verdict(trace: list[dict]) -> tuple[bool, str] | None:
    """``(passed, detail)`` from the waits when only waits ran since the baseline."""
    acts = _acts(trace)
    if not acts or any(_op(e) not in WAIT_OPS for e in acts):
        return None
    proofs = [e for e in acts if _op(e) in EVIDENCE_OPS]
    if not proofs:
        return None
    last = proofs[-1]
    nudge = "" if _nudged(trace) else " (" + NUDGE + ")"
    if str(last.get("outcome") or "") == "ok":
        return True, "verified by the wait: " + _op(last) + " finished" + nudge
    reason = str(last.get("detail") or last.get("outcome") or "it did not finish")
    return False, "the wait did not finish: " + reason[:200] + nudge


def _tail(trace: list[dict]) -> list[dict]:
    """The last non-wait action and the waits after it (the action under assertion)."""
    acts = _acts(trace)
    cut = max((n for n, e in enumerate(acts) if _op(e) not in WAIT_OPS), default=0)
    return acts[cut:]


def change_evidence(trace: list[dict]) -> str:
    """What proves the LAST action changed the screen although the ids are equal.

    A tap that starts a download leaves the a11y screen id alone but changes the
    tree (a progress bar); the ``wait_until_changed`` that follows it re-baselines
    against clock/cursor/animation noise and saw it. Only that trailing segment
    counts, and its last evidence wait decides, so a timeout fails. A raw hash
    move on a tap is noise, and an earlier action's change is a round trip.
    Empty when nothing proved a change: nothing happened is still a failure.
    """
    proofs = [e for e in _tail(trace) if _op(e) in EVIDENCE_OPS]
    if proofs and proofs[-1].get("outcome") == "ok":
        return "verified by the wait: " + _op(proofs[-1]) + " finished"
    return ""

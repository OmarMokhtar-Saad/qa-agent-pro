"""Verdict errors: device-error classes of a run and why an exploratory run stopped."""

from __future__ import annotations

from tools.mobile.explore_stop import display_stop
from tools.mobile.replay.screen import DEVICE_ERROR_OUTCOMES


# The overview KPI, the Logs breakdown and any case badge all read THIS dict. A section
# that counted its own rows would be a second derivation of one answer, and mirrored
# conditions drift -- which is why the pin monkeypatches this function and requires BOTH
# consuming sections to move.
#
# Only outcomes in ``executor.DEVICE_ERROR_OUTCOMES`` are counted. An outcome minted
# there and missing from that set fails the source scan in the pin, not silently here.
def error_classes(facts: object) -> dict:
    """``{outcome: count}`` over every trace entry of every case. ONE derivation."""
    counts: dict = {}
    for fact in facts if isinstance(facts, (list, tuple)) else ():
        rows = (fact or {}).get("rows") if isinstance(fact, dict) else None
        for row in rows if isinstance(rows, (list, tuple)) else ():
            outcome = (
                str((row or {}).get("outcome") or "") if isinstance(row, dict) else ""
            )
            if outcome in DEVICE_ERROR_OUTCOMES:
                counts[outcome] = counts.get(outcome, 0) + 1
    return counts


# ``explore_runner.display_stop`` is the callee, and the producer underneath it
# is ``stop_reason`` with THREE clauses: a recorded ``stop``, the turn budget,
# and the wall-clock deadline. The two names are one core with two contracts:
# ``display_stop`` is TOTAL because this is a RENDER path, while
# ``stop_reason`` stays strict because ``session.resolve``'s containment gate
# IS its raise. Reading
# ``explore["stop"]`` alone -- which this module used to do -- answers only the
# first, so a run whose deadline passed with no final turn read as still
# running forever. Measured on mrun-20260908-061316-a41b2a: the page said "in
# progress" 1h42m after the deadline, while ``session.resolve`` -- the other
# consumer of the same fact, at the anchor
# ``stop = explore_runner.stop_reason(explore)`` -- already said ``report``.
# Cited by ANCHOR and not by line: several sessions are moving that file, and
# source rots faster than a comment about it.
#
# ``""`` for a suite run: the question does not apply there, and the caller's
# own checkpoint arithmetic answers it.
def explore_stop(manifest: object) -> str:
    """Why this exploratory run has stopped, or ``""``. ONE producer."""
    # The RENDER contract, delegated rather than re-implemented. NOT
    # `stop_reason`: that one is strict on purpose -- it is `session.resolve`'s
    # containment gate -- and a manifest with a junk `deadline` reaching it from
    # here raised through `_is_partial` into `render`'s outer `except`, which
    # writes no page at all.
    return display_stop(manifest)

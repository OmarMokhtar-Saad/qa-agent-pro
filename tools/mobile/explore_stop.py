"""Leaf: the explore lane's stop contract (budgets, stop names, ``stop_reason``).

Cut from ``tools.mobile.explore_runner`` so ``charter`` and ``run_store`` can read
the stop constants and ``display_stop`` without importing an orchestrator. Imports
stdlib only. ``explore_runner`` re-exports every name here (identity).
"""

from __future__ import annotations

import logging
import time

# Keep the original logger name: log filters key on it.
logger = logging.getLogger("tools.mobile.explore_runner")

MAX_TURNS = 30
DEADLINE_S = 20 * 60
MAX_GOAL_CHARS = 600

STOP_GOAL = "goal_reached"
STOP_TURNS = "turn_budget_exhausted"
STOP_DEADLINE = "deadline_reached"  #: The charter's ``stop_on: first_finding``. A fifth stop, and the only one
#: step 4 wires: ``coverage_plateau`` is persisted and reported and behaves as
#: ``budget``, because nothing detects a plateau yet -- nothing grades a
#: plateau stop, follow-up.
STOP_FINDING = "first_finding"
#: The charter's ``destructive`` REFUSE, recorded as a stop. A refused run is
#: OVER: ``session._submit_explore`` writes this the moment the executor
#: reports a refusal, so ``stop_reason`` -- which returns any non-empty
#: ``state["stop"]`` -- and every page under it say the run stopped, rather
#: than leaving a terminal refusal looking like a turn the tester can answer.
STOP_GUARD_REFUSED = "guard_refused"


def _now(value: float | None) -> float:
    return time.time() if value is None else float(value)


# Why this session must stop, or ``""``. Checked BEFORE each turn.
#
# THE STRICT ONE, and its strictness is load-bearing. It coerces ``turn``,
# ``turns_budget`` and ``deadline``, and a state whose numbers are junk makes
# it RAISE. That raise is the containment gate: ``session.resolve`` calls this
# at the first statement of every entry point, its own handler turns the
# ValueError into ``{"error": ...}``, and so a corrupt state is refused before
# ``next_packet``, ``submit`` or ``qa_mobile_status`` can reach a device.
# ``test_l11_...`` and ``test_l12_...`` in
# ``tests/mobile/test_mobile_explore_turn_ledger.py`` pin exactly that -- L12
# states that the replay and the commit line are never reached.
#
# So DO NOT make this total. A render must never fail to draw a page because a
# field on disk is junk, and a gate must refuse exactly then: that is two
# contracts, and two contracts need two names over one core.
# :func:`display_stop` is the other name.
def stop_reason(state: object, *, now: float | None = None) -> str:
    """Why this session must stop, or ``""``. Checked BEFORE each turn."""
    body = state if isinstance(state, dict) else {}
    if str(body.get("stop") or ""):
        return str(body["stop"])
    if int(body.get("turn") or 0) >= int(body.get("turns_budget") or MAX_TURNS):
        return STOP_TURNS
    if _now(now) >= float(body.get("deadline") or 0):
        return STOP_DEADLINE
    return ""


# Why the run this MANIFEST describes has stopped, or ``""``. TOTAL.
#
# THE RENDER CONTRACT, and the counterpart to :func:`stop_reason`'s strict
# one. Same core -- it calls ``stop_reason`` -- with two differences that are
# the contract:
#
# * it takes the MANIFEST and does the lane check itself, so a consumer cannot
#   get "is this an explore run, and did it stop" half right;
# * it NEVER raises. A state whose numbers will not coerce reads as NOT
#   stopped, which is the answer the page gave before any of this existed. The
#   alternative is measured and worse: ``report._is_partial`` calls this, and
#   ``report.render``'s outer ``except`` turns any raise into NO PAGE AT ALL.
#
# Its callers are a page and a coverage count. The GATE keeps ``stop_reason``.
def display_stop(manifest: object, *, now: float | None = None) -> str:
    """Why the run this MANIFEST describes has stopped, or ``""``. TOTAL."""
    body = manifest if isinstance(manifest, dict) else {}
    if str(body.get("lane") or "") != "explore":
        return ""
    explore = body.get("explore")
    try:
        return stop_reason(explore if isinstance(explore, dict) else {}, now=now)
    except (TypeError, ValueError, OverflowError):
        logger.warning(
            "mobile.explore_runner: unreadable explore state, displaying as running"
        )
        return ""

"""Was the goal already met when the tester stopped the run?

``qa_mobile_stop`` used to record ``unverified`` for every run it ended, even a
run whose last case had already passed (run mrun-20261016-093543-119b8d: login
met, "Welcome LUBNA" on the home screen, then the tester stopped it). The stop
is not evidence of anything; the CASES are. This module reads only what the
record already holds and answers with the same ``{verdict, reason}`` shape a
``done`` trace uses, so ``run_verdict.synthesize`` treats it like any other pass.

Goal met means ALL of: the last case is ``done`` with verdict ``pass`` (the
replay executor only says ``pass`` when an assert held or the screen moved, a
crash already downgrades it to ``fail``), every case is a pass or has no
verdict yet (``fail``, ``blocked``, ``unverified`` or any other verdict spoils
it), and no case was stopped by the destructive guard. Anything else returns ``None`` and the run
stays ``unverified``: an outcome nothing proved is unknown.

Pure reads, total: never raises.
"""

from __future__ import annotations

from typing import Optional

from tools.mobile import run_store
from tools.mobile.replay.context import STATUS_DONE
from tools.mobile.verdicts import VERDICT_PASS

#: Appended to the reason so a reader sees the TESTER ended the run.
STOPPED_AFTER_GOAL = " (stopped after goal)"
_MAX_REASON = 200
#: Case verdicts that do not spoil a stop: a pass, or no verdict yet.
_CLEAN_VERDICTS = frozenset({VERDICT_PASS, ""})


def _guarded(case: dict) -> bool:
    stop = case.get("guard_stop")
    return isinstance(stop, dict) and any(stop.get(k) for k in ("term", "op", "node"))


def _spoiled(case: dict) -> bool:
    return (case.get("verdict") or "") not in _CLEAN_VERDICTS or _guarded(case)


def _passed(case: dict) -> bool:
    return case.get("status") == STATUS_DONE and case.get("verdict") == VERDICT_PASS


def _cases(run_id: str) -> list[dict]:
    try:
        body = (run_store.list_cases(run_id) or {}).get("content")
    except (OSError, ValueError, AttributeError):
        return []
    return [c for c in body if isinstance(c, dict)] if isinstance(body, list) else []


def goal_verdict(run_id: str) -> Optional[dict]:
    """``{verdict: pass, reason}`` when the record proves the goal, else None."""
    cases = _cases(run_id)
    if not cases or any(_spoiled(c) for c in cases) or not _passed(cases[-1]):
        return None
    reason = " ".join(str(cases[-1].get("reason") or "goal met").split())
    return {
        "verdict": VERDICT_PASS,
        "reason": reason[:_MAX_REASON] + STOPPED_AFTER_GOAL,
    }

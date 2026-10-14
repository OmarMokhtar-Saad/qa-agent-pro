"""The outcome of a replayed step: the verdict, a tester request, or an escape."""

from __future__ import annotations

from typing import Any

from tools.mobile import run_packets as mobile_run
from tools.mobile import run_store
from tools.mobile.case_run_checkpoint import _step_payload
from tools.mobile.case_run_consts import NEEDS_TESTER
from tools.mobile.case_run_counts import escapes_used
from tools.mobile.case_run_escape import _escape_checkpoint
from tools.mobile.case_run_types import _Step
from tools.mobile.replay.context import STATUS_DONE, STATUS_ERROR, STATUS_NEEDS_TESTER
from tools.mobile.replay.guard import MAX_GUARD_NODE_CHARS
from tools.mobile.verdicts import (
    VERDICT_BLOCKED,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_UNVERIFIED,
)
from tools.mobile_evidence import crash_detector


def _crash_override(verdict: str, reason: str, crash: object) -> tuple[str, str]:
    """``(verdict, reason)`` after the app's own death is taken into account.

    THE one place a detected crash overrides a verdict, and it is deliberately a
    function of three values with no I/O: nothing else in this tree re-derives
    it, and the report and the chat read the STORED verdict and reason instead.

    * It fires ONLY on ``pass``. A ``fail``/``blocked``/``unverified`` has nothing
      to downgrade, and rewriting its reason would destroy the model's own
      finding.
    * It runs AFTER the ``VERDICT_*`` normalisation, so a verdict this server
      could not read stays ``blocked``: a crash must never promote an unreadable
      verdict into a judgement.
    * The reason it writes says the SERVER made the call, first, and quotes the
      model's own words last -- see ``crash_detector.server_reason``.
    """
    body = crash if isinstance(crash, dict) else {}
    if verdict != VERDICT_PASS or not body.get("detected"):
        return verdict, reason
    return VERDICT_FAIL, crash_detector.server_reason(body, reason)


def _read_step(run_id: str, tc_id: str, replayed: dict) -> _Step:
    """Unpack a replay's result and persist the screen it ended on."""
    result = replayed.get("content") or {}
    trace = list(result.get("trace") or [])
    new_screen: Any = (
        result.get("screen") if isinstance(result.get("screen"), dict) else {}
    )
    used = escapes_used(run_id, tc_id)
    if new_screen:
        run_store.write_screen(run_id, new_screen)
    return _Step(run_id, tc_id, result, trace, used, new_screen)


def _terminal_verdict(result: dict) -> str:
    """The executor's verdict, defaulting to pass and clamped to a known one."""
    verdict = str(result.get("verdict") or "") or VERDICT_PASS
    known = (VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED, VERDICT_UNVERIFIED)
    return verdict if verdict in known else VERDICT_BLOCKED


def _guard_stop(result: dict) -> dict:
    """The guard stop a tester request records, each field clipped."""
    return {
        "term": str(result.get("guard_term") or "")[:80],
        "op": str(result.get("guard_op") or "")[:40],
        "node": str(result.get("guard_node") or "")[:MAX_GUARD_NODE_CHARS],
    }


def _tester_ask(step: _Step) -> mobile_run.TesterAsk:
    """The ask the stopped step puts to the tester (a field, or a guard go-ahead)."""
    result = step.result
    return mobile_run.TesterAsk(
        str(result.get("field") or ""),
        str(result.get("reason") or ""),
        run_id=step.run_id,
        tc_id=step.tc_id,
        guard_term=str(result.get("guard_term") or ""),
    )


def _tester_payload(step: _Step, view: dict) -> dict:
    """The needs_tester outcome: a credential request and the guard that stopped."""
    result = step.result
    reason = str(result.get("reason") or "")
    packet = mobile_run.build_tester_request(_tester_ask(step))
    return _step_payload(
        step,
        view,
        verdict="",
        status=NEEDS_TESTER,
        reason=reason,
        packet=packet,
        guard_stop=_guard_stop(result),
    )


def _outcome_checkpoint(step: _Step, view: dict, crash: object) -> dict:
    """The checkpoint for a replayed step, by the status the executor ended on."""
    status = str(step.result.get("status") or "")
    if status == STATUS_DONE:
        return _done_payload(step, view, crash)
    if status == STATUS_NEEDS_TESTER:
        return _tester_payload(step, view)
    if status == STATUS_ERROR:
        return _step_payload(
            step,
            view,
            verdict=VERDICT_BLOCKED,
            status=STATUS_ERROR,
            reason=str(step.result.get("reason") or ""),
            packet=None,
        )
    return _escape_checkpoint(step, view)


# A model-declared `pass` over a case whose app DIED is a false pass, and the
# lane captured the proof and then never read it. The override happens here
# and nowhere else: this is already the one place a terminal verdict is
# normalised, so a second site would be a second answer to the same question.
def _done_payload(step: _Step, view: dict, crash: object) -> dict:
    """The done outcome: the normalised verdict, crash-overridden."""
    result = step.result
    reason = str(result.get("reason") or "")
    verdict, done_reason = _crash_override(_terminal_verdict(result), reason, crash)
    return _step_payload(
        step,
        view,
        verdict=verdict,
        status=STATUS_DONE,
        reason=done_reason,
        packet=None,
    )

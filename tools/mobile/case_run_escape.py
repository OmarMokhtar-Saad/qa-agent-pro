"""The needs_model outcome: charge or exempt the stop, then ask or give up."""

from __future__ import annotations

from tools.mobile import run_packets as mobile_run
from tools.mobile.case_run_checkpoint import _step_payload
from tools.mobile.case_run_consts import (
    BUDGET_CAP_REASON,
    ESCAPE_CAP_REASON,
    MAX_ESCAPES,
    NEEDS_MODEL,
    REASON_BUDGET,
    REASON_SELECTOR,
    REASON_VISUAL,
    UNCHARGED_CAPS,
)
from tools.mobile.case_run_counts import uncharged_stops
from tools.mobile.case_run_types import _Ask, _Step
from tools.mobile.verdicts import VERDICT_BLOCKED


# Which exempt-from-escape reason a needs_model stop carries, else ``""``.
#
# TWO reasons a stop is not charged as an escape, ONE mechanism.
#
# A BUDGET STOP IS NOT AN ESCAPE. The executor stops a replay that would
# outlive the client's tool timeout; the script was legal, the actions
# that ran are in the trace, and the model is being asked to continue
# rather than to re-plan. Charging one of three escapes for obeying our
# own bound turns a correct case into `blocked`.
#
# A STALE-SELECTOR STOP IS NOT AN ESCAPE EITHER, when nothing touched
# the device first: `perception` mints the id, and the app moving an
# element is not the tester's case failing to make progress.
#
# The caps differ because the evidence differs -- a long case
# legitimately budget-stops many times, a plan whose own selector went
# stale should not get many retries -- so they are held per reason in
# UNCHARGED_CAPS rather than as one number. One counter, one disclosure,
# one carry-forward; two bounds, neither laxer than it was alone.
#
# ONE number per reason. The cap check used to read MAX_BUDGET_STOPS while
# the exemption read UNCHARGED_CAPS, so for the budget reason the dict
# entry was INERT -- measured 2026-09-04: raising it to 99 changed no
# behaviour and passed all 1410 mobile tests, while raising the constant
# was caught by the peer's own static guard. A redundant bound that reads
# as authoritative is how a later retune of the unified counter silently
# does nothing.
def _uncharged_reason(result: dict) -> str:
    """Which exempt-from-escape reason a needs_model stop carries, else ``""``."""
    if result.get("budget_stop"):
        return REASON_BUDGET
    if bool(result.get("selector_stale")) and not result.get("actuated"):
        return REASON_SELECTOR
    if result.get("visual_check"):
        return REASON_VISUAL
    return ""


def _budget_capped(reason_key: str, uncharged: dict) -> bool:
    """Whether this stop just used up the budget-stop allowance."""
    return (
        reason_key == REASON_BUDGET
        and uncharged[reason_key] >= (UNCHARGED_CAPS[reason_key])
    )


def _budget_cap_payload(step: _Step, view: dict, uncharged: dict) -> dict:
    """The blocked outcome for a case that spent its whole budget-stop allowance."""
    return _step_payload(
        step,
        view,
        verdict=VERDICT_BLOCKED,
        status=VERDICT_BLOCKED,
        reason=BUDGET_CAP_REASON,
        packet=None,
        uncharged=uncharged,
    )


# No `uncharged=` at the escape-cap call site, and that is deliberate rather
# than an omission. This exit is only reached by ORDINARY escapes, on whose
# branch the counter is read straight from disk and never incremented -- so
# passing it is identical to the carry-forward default, which mutation proved
# by deleting it and changing no result. The cap exit above, in
# `_budget_cap_payload`, passes it because that is the branch that increments.
def _escape_cap_payload(step: _Step, view: dict, ask: _Ask) -> dict:
    """The blocked outcome for a case that spent every escape it was given."""
    reason = str(step.result.get("reason") or "")
    return _step_payload(
        step,
        view,
        verdict=VERDICT_BLOCKED,
        status=VERDICT_BLOCKED,
        reason=ESCAPE_CAP_REASON + " Last stop: " + reason,
        escapes=ask.used,
        packet=None,
        uncharged=ask.uncharged,
    )


# A fresh budget stop's real remainder, and ONLY that -- a selector-stale or
# ordinary escape stop clears the queue by passing None, same as every other
# checkpoint that omits this keyword.
def _fresh_queue(result: dict, reason_key: str) -> object:
    """The queue a budget stop hands back, else ``None``."""
    return result.get("queued_actions") if reason_key == REASON_BUDGET else None


def _escape_packet(step: _Step, view: dict, ask: _Ask) -> dict:
    """The escape-hatch job the model is asked to answer."""
    return mobile_run.build_escape_job(
        view,
        step.new_screen,
        step.trace,
        run_id=step.run_id,
        tc_id=step.tc_id,
        escapes=ask.used,
        reason=str(step.result.get("reason") or ""),
    )


def _ask_model_payload(step: _Step, view: dict, ask: _Ask) -> dict:
    """Hand the model the escape-hatch job for a stop that is still allowed."""
    result = step.result
    return _step_payload(
        step,
        view,
        verdict="",
        status=NEEDS_MODEL,
        reason=str(result.get("reason") or ""),
        escapes=ask.used,
        packet=_escape_packet(step, view, ask),
        uncharged=ask.uncharged,
        queued_actions=_fresh_queue(result, ask.reason_key),
    )


def _escape_checkpoint(step: _Step, view: dict) -> dict:
    """The needs_model outcome: charge or exempt the stop, then ask or give up."""
    used = step.used
    uncharged = uncharged_stops(step.run_id, step.tc_id)
    reason_key = _uncharged_reason(step.result)
    if reason_key and uncharged[reason_key] < UNCHARGED_CAPS[reason_key]:
        uncharged[reason_key] += 1
        # ONE number per reason: see `_uncharged_reason`.
        if _budget_capped(reason_key, uncharged):
            return _budget_cap_payload(step, view, uncharged)
    else:
        used += 1
    ask = _Ask(used, uncharged, reason_key)
    if used >= MAX_ESCAPES:
        return _escape_cap_payload(step, view, ask)
    return _ask_model_payload(step, view, ask)

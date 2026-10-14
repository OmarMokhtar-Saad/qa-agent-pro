"""Mobile session submit: parse the explore reply and arm its guards (split from session_submit)."""

from __future__ import annotations

import logging

from tools.mobile import actions as actions_mod
from tools.mobile import carry_policy, case_runner
from tools.mobile import charter as charter_mod
from tools.mobile.replay.context import Context
from tools.mobile.session_core import STATE_RUNNING
from tools.mobile.session_explore_ids import explore_turn_case_id
from tools.mobile.session_explore_prior import (
    _explore_prior_turn_blocked,
    _explore_prior_verified,
)
from tools.mobile.session_explore_stop import _prior_guard_stop_explore
from tools.mobile.session_submit_inputs import _combine_queued_actions

logger = logging.getLogger("tools.mobile.session")


def _explore_needs_model(reason: str, resolved: dict) -> dict:
    """A refused explore turn: logged, then handed back to the model to rewrite."""
    case_runner.note_refusal(reason)
    return {
        "error": None,
        "content": {
            "state": STATE_RUNNING,
            "case": {"status": case_runner.NEEDS_MODEL, "reason": reason},
            "packet": None,
            "next": {},
            "field": "",
            "resolved": resolved,
        },
    }


def _prime_explore_turn(run_id: str, ctx: Context, payload: dict) -> None:
    """Expect the apps this turn names; carry the screen only after an inert turn."""
    ctx.expect_apps = tuple(actions_mod.expect_apps_from(payload))
    ctx.carry_screen = carry_policy.allowed(run_id)


def _noted_outcome(run_id: str, replayed: dict) -> dict:
    """The replay's content, after noting whether the turn actuated anything."""
    outcome = replayed.get("content") or {}
    carry_policy.note(run_id, outcome.get("trace"))
    return outcome


def _queued_in(resolved: dict) -> list:
    """The actions a prior budget stop left unreached on the explore state."""
    return list((resolved.get("explore") or {}).get("queued_actions") or [])


# SERVER-SIDE queue (defect F2): actions a prior budget stop left unreached
# ride the persisted explore state and run FIRST, ahead of whatever the
# model just submitted -- see _combine_queued_actions. Returns the refusal
# REASON (possibly "") or None; a folded payload carries the combined actions.
def _fold_queue(payload: dict, resolved: dict) -> str | None:
    """Fold the queued actions ahead of the payload's own; the refusal reason, if any."""
    queued_in = _queued_in(resolved)
    if not queued_in:
        return None
    combined = _combine_queued_actions(queued_in, list(payload.get("actions") or []))
    if not combined.get("ok"):
        return str(combined.get("reason") or "")
    payload["actions"] = combined["actions"]
    return None


# DECODED, not type-tested. `qa_submit_mobile_step` declares `script: str`,
# so this used to be `{}` on every real client path: the whole JSON string
# went to `parse_script`, `Script`'s `extra="forbid"` refused the advertised
# `finding` key, and nothing replayed -- and when a model retried with a
# bare actions array, `apply_turn_result` got `{}` and never read the
# finding. Two failures, one cause, and no test saw either because every
# test passed a dict into this helper.
#
# A folded queue was already held to both caps by the combine, so only a
# bare submission is held to the model cap here.
def _parse_explore_submission(
    raw: object, resolved: dict
) -> tuple[dict, dict, dict | None]:
    """Decode and parse the model's reply: (payload, parsed script, refusal)."""
    payload = actions_mod.decode_reply(raw)
    reason = _fold_queue(payload, resolved)
    if reason is not None:
        return payload, {}, _explore_needs_model(reason, resolved)
    cap = (
        actions_mod.MAX_ACTIONS
        if _queued_in(resolved)
        else actions_mod.MAX_MODEL_ACTIONS
    )
    parsed = actions_mod.parse_script(payload.get("actions"), max_actions=cap)
    if parsed.get("error"):
        return payload, parsed, _explore_needs_model(parsed["error"], resolved)
    return payload, parsed, None


# WHAT HAPPENS AFTER A GUARD HIT, from this run's own charter. The guard
# itself is unchanged and no value here lets an action through it:
# ``charter.guard_policy`` maps ``destructive: none`` to REFUSE, and the
# executor then ENDS the attempt by name instead of pausing for the
# tester. An explicit input, set beside ``prior_verified`` for the same
# reason -- the executor knows nothing about runs or charters.
def _guard_refuses(resolved: dict) -> bool:
    """Whether this run's charter makes a guard hit end the attempt."""
    destructive = ((resolved.get("explore") or {}).get("charter") or {}).get(
        "destructive"
    )
    return charter_mod.guard_policy(destructive) == charter_mod.REFUSE


# Run-wide evidence, read from the checkpoints this run already wrote and
# passed in EXPLICITLY. See ``_explore_prior_verified``.
#
# THIS turn's own id, computed BEFORE the replay. The exact-match
# lookup alone no longer suffices here: `explore_runner.next_packet`
# bumps `state["turn"]` on EVERY call, so a confirm submitted after the
# scheduler already advanced past this turn names a `tc_id` no case was
# ever checkpointed under. See `_prior_guard_stop_explore`.
def _arm_explore_guards(run_id: str, ctx: Context, resolved: dict) -> None:
    """Set the run-wide evidence and guard policy the executor reads."""
    ctx.prior_turn_blocked = _explore_prior_turn_blocked(run_id)
    ctx.prior_verified = _explore_prior_verified(run_id)
    ctx.guard_refuses = _guard_refuses(resolved)
    ctx.prior_guard_stop = _prior_guard_stop_explore(
        run_id, explore_turn_case_id(resolved.get("explore"))
    )

"""Mobile session submit: tester inputs, budget cap and call preparation (split from session_submit)."""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import actions as actions_mod
from tools.mobile import held_inputs, lock_reaper
from tools.mobile.replay.context import SUBMIT_BUDGET_S, Context
from tools.mobile.session_audit import MOBILE_MAX_TESTER_INPUTS
from tools.mobile.session_core import PACKET_RESERVE_S
from tools.mobile.session_runs import context_for
from tools.mobile.session_submit_args import SubmitCall

logger = logging.getLogger("tools.mobile.session")


# Actions left queued by a budget stop, folded with a fresh submit.
#
# Delegates to :func:`tools.mobile.actions.combine_queued_actions` -- FU1
# moved the combine rule there so ``case_runner`` (which cannot import
# ``session``) can use the same rule for the SCRIPTED lane. Behaviour-
# identical; see that function's docstring for the queue-first,
# cap-refuses-by-name design.
def _combine_queued_actions(queued: object, new_actions: object) -> dict:
    """Actions left queued by a budget stop, folded with a fresh submit."""

    return actions_mod.combine_queued_actions(queued, new_actions)


def _clean_name(raw: object) -> str:
    """A field name: stripped and truncated to 80 chars (the legacy cap)."""
    return str(raw or "").strip()[:80]


def _check_cap(merged: dict) -> None:
    """Raise ``ValueError`` naming the cap when *merged* holds too many fields."""
    if len(merged) > MOBILE_MAX_TESTER_INPUTS:
        raise ValueError(
            "this submit names %d tester fields; one submit takes at most %d "
            "(MOBILE_MAX_TESTER_INPUTS). Nothing was run: answer only the "
            "fields this screen asks for and send the rest on a later submit."
            % (len(merged), MOBILE_MAX_TESTER_INPUTS)
        )


# The legacy single ``(field, value)`` pair plus a multi-field dict, merged
# into ONE dict :func:`submit` puts on ``Context.tester_inputs``.
#
# ``tester_inputs`` wins on a colliding field name -- it is the newer, more
# specific form, and a caller sending both is choosing to override the
# legacy pair rather than fighting it silently. Each field name is
# truncated to 80 chars, matching the legacy single-field cap. More than
# :data:`MOBILE_MAX_TESTER_INPUTS` fields raises ``ValueError`` naming the
# cap -- :func:`submit` returns it as the error, and runs nothing -- rather
# than dropping fields silently, so an oversized payload cannot grow the audit's ``tester_fields`` list, or the
# executor's per-field lookups, without bound.
def _merge_tester_inputs(
    tester_input: str, tester_input_field: str, tester_inputs: dict | None
) -> dict:
    """The legacy single field pair plus a multi-field dict, merged into one dict."""
    merged: dict = {}
    legacy_field = _clean_name(tester_input_field)
    if legacy_field and str(tester_input or ""):
        merged[legacy_field] = str(tester_input)
    if isinstance(tester_inputs, dict):
        for raw_name, value in tester_inputs.items():
            name = _clean_name(raw_name)
            if name and str(value or ""):
                merged[name] = str(value)
    _check_cap(merged)
    return merged


def _route_expect_tuple(route_expect: object) -> tuple[str, ...]:
    """The expected route as a tuple of strings; empty for anything else."""
    if isinstance(route_expect, (list, tuple)):
        return tuple(str(item) for item in route_expect)
    return ()


def _cap_submit_budget(ctx: Context, budget: Any) -> None:
    """Cap this submit's replay budget so ``PACKET_RESERVE_S`` is left over."""
    if budget is None:
        return
    # Caps THIS submit's replay so PACKET_RESERVE_S is still there for
    # next_packet afterward -- without this, a slow action can spend
    # the whole call budget on the replay and next_packet's busy
    # reply is left with nothing to build a packet from. Never above
    # SUBMIT_BUDGET_S: that constant is unchanged and still the
    # ceiling.
    ctx.call_deadline = getattr(budget, "deadline", None)
    ctx.budget_s = max(
        1.0,
        min(SUBMIT_BUDGET_S, budget.remaining() - PACKET_RESERVE_S),
    )


# HELD FOR THE RUN (fix round 3, item 1). On the Air run the tester
# gave the login fields once and was asked again on five of eight
# turns, because a value lived on the Context for ONE call. It is now
# held in process memory by `held_inputs` -- never on disk, and never
# in this module, which keeps no state in memory -- and every later
# submit of the same run carries it, so `ask_tester` finds it already
# supplied. A value given on THIS call wins over a held one. The hold
# is dropped when the SUBMIT ITSELF reaches the report (both lanes,
# below); any other way a run ends relies on the sliding TTL.
def _hold_tester_inputs(run_id: str, ctx: Context, merged: Any) -> None:
    """Remember this call's inputs for the run and put every held one on *ctx*."""

    held_inputs.remember(run_id, merged)
    carried = held_inputs.recall(run_id)
    if carried or merged:
        ctx.tester_inputs = dict(carried, **dict(merged or {}))


# S10 saved route (PROVISIONAL): empty unless the wrapper is replaying a route.
def prepare_submit(
    call: SubmitCall, resolved: dict
) -> tuple[Context, dict, str] | dict:
    """The replay Context, run body and field name for *call*, or an error dict."""
    lock_reaper.touch_activity(call.run_id)
    body = resolved["content"] or {}
    ctx = context_for(body)
    _cap_submit_budget(ctx, call.budget)
    field = _clean_name(call.tester_input_field)
    try:
        merged = _merge_tester_inputs(
            call.tester_input, call.tester_input_field, call.tester_inputs
        )
    except ValueError as exc:
        return {"error": str(exc), "content": None}
    _hold_tester_inputs(call.run_id, ctx, merged)
    ctx.confirm_destructive = bool(call.confirm_destructive)
    ctx.route_expect = _route_expect_tuple(call.route_expect)
    return ctx, body, field

"""Carry-forward verdicts: what a re-prepare does about the previous prep's screens.

``carry_forward_or_refuse`` decides, PER CHANNEL, what a re-prepare does about the
previous prep's screens: ``(capture_ids, carried_ids, carried_from, note, refusal)``.

Three outcomes, and the PARTIAL one is why this is a function rather than a
boolean: whatever came back off the carry-forward shelf is ALWAYS kept (an
unassigned revival would sit orphaned in the tray with a stale created_at,
first in line for eviction, while the reply claimed every screen was lost).

  * full recovery     -> ids + a carry-forward note, no refusal
  * shortfall, no ack -> ids + a refusal describing ONLY the shortfall
  * shortfall, acked  -> ids + a note naming what could not be recovered

The chat-ATTESTED channel is never recoverable -- those bytes never reached
this server -- so it always contributes to the shortfall. Never raises: on
any internal error every element comes back empty and the prepare proceeds
exactly as it does today, because a guard must fail OPEN.
"""

from __future__ import annotations

import logging

from agents.host_gen_args import CarryAsk
from config.settings_guard import never_raise
from handlers import image_gate
from handlers.capture_carry_plan import _carry_plan, _CarryPlan

logger = logging.getLogger("tools.mcp_handlers")


def _carry_verdict(
    plan: "_CarryPlan", image_carry_ack: bool, prep: dict | None
) -> tuple:
    """``(note, refusal)`` for a plan with a recovery or a shortfall."""
    if plan.short_total <= 0:
        # W4: recovered_cap counts screens RESTORED to the tray, not screens that
        # will fit the reply; `_budget_keep_flags` names every image it drops.
        # The count here is deliberately the RECOVERY, not the shipment.
        return (
            f"> \U0001f4f8 Carried forward {plan.recovered_cap} device "
            f"screen(s) from the previous preparation `{plan.from_prep}` for "
            "this same "
            "source: this call did not carry them, and generating "
            "without them would have silently dropped the grounding. "
            "Pass `image_carry_ack=true` to generate WITHOUT them "
            "instead."
        ), ""
    if not image_carry_ack:
        return "", _carry_refusal(plan, prep)
    return _carry_acked_note(plan), ""


def _carry_refusal(plan: "_CarryPlan", prep: dict | None) -> str:
    return image_gate._reprep_image_loss_refusal(
        image_gate._RefusalReq(
            prep_id=plan.from_prep or "?",
            age_s=plan.age_s,
            # The per-channel SHORTFALL, not the prior prep's totals: the refusal
            # must describe what would go missing on this call.
            captured=plan.gap_cap,
            attested=plan.gap_att,
            recovered=plan.recovered_cap,
            # ... and the prior prep's TOTALS separately (review M2).
            prior_captured=plan.prior_shipped_cap,
            prior_attested=plan.prior_att,
            # What this call DOES carry (review C1) + the credited shortfall.
            carries_captured=plan.resolved_cap,
            carries_attested=plan.resolved_att,
            shortfall=plan.short_total,
            labels=list((prep or {}).get("captured_image_labels") or []),
        )
    )


def _carry_acked_note(plan: "_CarryPlan") -> str:
    # L1: "the screen(s) the previous preparation had" is a PRIOR TOTAL, not
    # the sum of the two GAPS.
    prior_total = plan.prior_shipped_cap + plan.prior_att
    substitute_clause = (
        f" {plan.surplus_cap} other screen(s) on this call were counted in their place."
        if plan.surplus_cap
        else ""
    )
    return (
        f"> ⚠️ Generating with {plan.identity_cap} of "
        f"the {prior_total} screen(s) the previous preparation "
        f"`{plan.from_prep}` for this same source had: `image_carry_ack=true` "
        f"was sent and {plan.short_total} could not be recovered (chat "
        "attachments never reach this server, and captured screens "
        f"expire).{substitute_clause} Anything that exists only in those "
        f"{plan.short_total} screen(s) is NOT reflected in the cases below."
    )


def _nothing_to_carry(plan: "_CarryPlan | None") -> bool:
    # Nothing to carry, or nothing carried and nothing missing: this call
    # simply carries the screens on a different channel (review C1).
    # Silent by design -- a note about a substitution the tester made
    # deliberately is noise.
    return plan is None or (plan.short_total <= 0 and not plan.recovered_cap)


def _refuse_or_note(plan: "_CarryPlan", image_carry_ack: bool, prep: dict | None):
    note, refusal = _carry_verdict(plan, image_carry_ack, prep)
    return (plan.merged_ids, list(plan.revived_ids), plan.from_prep, note, refusal)


def carry_forward_or_refuse(ask: CarryAsk) -> tuple:
    """Decide, PER CHANNEL, what a re-prepare does about the previous prep's
    screens (see the module docstring); never raises."""
    prep, image_carry_ack, capture_ids, have_attested = ask
    with never_raise(logger, "carry_forward_or_refuse failed", exc_info=True):
        plan = _carry_plan(prep, capture_ids, have_attested)
        if plan is None or _nothing_to_carry(plan):
            return ([], [], "", "", "")
        return _refuse_or_note(plan, image_carry_ack, prep)
    return ([], [], "", "", "")

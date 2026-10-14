"""Jira image gate: the BEAT 1 elicitation tiers and the beat 2 skip confirmation."""

from __future__ import annotations

import logging
import sys

from handlers import _common, _elicit
from handlers.image_gate_labels import (
    _IMAGE_GATE_ATTACH_LABEL,
    _IMAGE_GATE_SKIP_LABEL,
    _IMAGE_SOURCE_LABELS,
)
from handlers.image_gate_plan import _normalize_source_plan
from handlers.seam_alias import alias

logger = logging.getLogger("tools.mcp_handlers")


def _skip_why(res) -> str:
    """Return why the tester's answer did not clear the skip confirmation."""
    if res.status == _common.CHOSEN:
        return "The tester chose to attach the screens first."
    if res.status == _common.DECLINED:
        return "The tester dismissed the confirmation dialog."
    return (
        "The tester could not be asked (this client shows no dialog, or no "
        "answer arrived), and `image_gate_ack=true` alone cannot skip the "
        "screens. Attach them instead."
    )


# Ask the TESTER whether beat 2 may be skipped. Returns "" only on a CHOSEN
# skip; otherwise ``beat2`` plus a line saying why the ack did not clear it.
# Never raises (elicit_choice degrades to UNAVAILABLE).
async def _confirm_image_gate_skip(choose: _common.ChooseCb, beat2: str) -> str:
    """Return "" on a chosen skip, else beat 2 plus why the ack did not clear it."""
    res = await _elicit.elicit_choice(
        choose,
        "This ticket has screens I was not given. Generate from the ticket text only?",
        [_IMAGE_GATE_SKIP_LABEL, _IMAGE_GATE_ATTACH_LABEL],
    )
    if res.status == _common.CHOSEN and res.value == _IMAGE_GATE_SKIP_LABEL:
        return ""
    return f"{beat2}\n\n{_skip_why(res)}"


_ENUM_PROMPT = (
    "I cannot read images out of Jira -- where do this ticket's screens come from?"
)
_TEXT_PROMPT = (
    "I cannot read images out of Jira, only text. Where do the screens come "
    "from? Reply with one of: jira (text only), jira_attach (you attach them "
    "here), jira_device (capture from a connected device), jira_both, "
    "device."
)


# BEAT 1 elicitation, returning the plan AND why it resolved that way.
#
# Tier 1 the enum dialog; tier 2 a free-text prompt (Cursor 3.12 auto-cancels
# enum dialogs but still renders text prompts); tier 3 is the caller relaying
# _image_gate_menu_markdown when the plan comes back "". Never raises, never
# dead-ends, and the ORDER and NUMBER of elicitation attempts is exactly what
# it was before this function existed -- only the reported label is new.
#
# 2026-08-09 (Batch 3, FIX 3): the second return value is the DIAGNOSTIC this
# beat lacked. The audit row for the 08:18:23 run said only
# {"resolved": false, "plan": ""}, and BOTH degradation paths log at DEBUG
# while the installed log file runs at INFO, so there was no way to tell why.
#
# WHY THE LABEL IS NOT JUST THE ChoiceResult (review H3): elicit_choice returns
# UNAVAILABLE for a MISSING callback *and* for a raising ctx.elicit (the module
# comment above ChoiceResult already flags this), and the elicitation gate
# used to default FALSE -- so on a stock install the label would read
# "unavailable/unavailable", indistinguishable from the client-capability limit
# it exists to identify. The flag/callback state is the only thing that can tell
# those apart, so an unresolved gate on an install whose elicit seam was
# off was reported as "disabled/disabled"; the seam is deleted, but that label
# is still reachable when choose and ask_text are both None on an ungated
# client, and everything else keeps the honest
# "<enum-status>/<text-status>" form ("unavailable/unavailable" for a client
# that cannot show dialogs, "declined/..." for a tester who dismissed one).
async def elicit_source_plan_status(
    choose: _common.ChooseCb, ask_text: _elicit.AskCb
) -> tuple[str, str]:
    """Return the beat 1 plan and why it resolved that way; never raises."""
    _disabled = _elicit._elicitation_disabled(choose, ask_text)
    picked = await _elicit.elicit_choice(
        choose, _ENUM_PROMPT, list(_IMAGE_SOURCE_LABELS)
    )
    if picked.status == _common.CHOSEN:
        plan = _IMAGE_SOURCE_LABELS.get(picked.value or "", "")
        if plan:
            return plan, f"{picked.status}/skipped"
    asked = await _elicit._elicit_text(ask_text, _TEXT_PROMPT)
    return _text_tier_result(picked.status, asked, _disabled)


def _text_tier_result(enum_status: str, asked, disabled: bool) -> tuple[str, str]:
    """Plan and status once the free-text tier has answered (or not)."""
    status = f"{enum_status}/{asked.status}"
    if asked.status == _common.CHOSEN:
        plan = _normalize_source_plan(asked.value or "")
        if plan:
            return plan, status
        # Answered but unusable -- report it as ANSWERED, never as "disabled".
    elif disabled:
        status = "disabled/disabled"
    logger.info(
        "image gate beat 1: no source plan from elicitation (%s) -- relaying the "
        "markdown menu instead; the host must re-call with source_plan",
        status,
    )
    return "", status


# The plan-only form, for callers that do not need the status.
#
# A thin delegate to elicit_source_plan_status, so the signature every
# existing caller and test uses is unchanged. NOTE for future maintainers: the
# beat-1 call site uses the STATUS form, so a test that monkeypatches beat-1
# elicitation must patch BOTH names or it silently asserts nothing (this is why
# tests/test_jira_image_gate.py patches both). Never raises, never dead-ends.
async def elicit_source_plan(choose: _common.ChooseCb, ask_text: _elicit.AskCb) -> str:
    """Return the plan alone, as a thin delegate to the status form; never raises."""
    plan, _status = await elicit_source_plan_status(choose, ask_text)
    return plan


_elicit_source_plan = alias(sys.modules[__name__], "elicit_source_plan")
_elicit_source_plan_status = alias(sys.modules[__name__], "elicit_source_plan_status")

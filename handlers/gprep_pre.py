"""Small pre-steps of the PREPARE front half: mode, revive, beat 1, nudge."""

from __future__ import annotations

import logging

import llm
from handlers import capture, generate
from handlers.generate_args import PlanNudgeArgs

logger = logging.getLogger("tools.mcp_handlers")


# Probe re-sent ids, then the duplicate-prep guard. Nothing in here moves
# a screen off the carry-forward shelf except carry_forward_or_refuse's
# own revive (see "Deferred REVIVE" below).
#
# The open-prep clarify above stays dismissible by `proceed_anyway=true`;
# the IMAGE-loss refusal is not, because it has its own ack.
# F22: SHAPE before SCREENS. A board / dashboard / site-root URL on a Jira
# host names no ticket, so there are no ticket screens to ask about -- and
# _jira_page_without_issue_note fetches nothing, so this costs one string
# test. Placed after the open-prep guard (cheaper still, and it can
# end the call itself) and deliberately BEFORE the deferred revive
# below: a round that refuses must not first move a screen off the
# carry-forward shelf, which is the same rule the revive's own comment
# states for the clarify above it.
#
# Deferred REVIVE (review M3). NOTHING above this point may move a
# screen off the carry-forward shelf: the clarify above is a
# dismissible RETRY, and consuming the shelf on a round that returned a
# clarify destroyed the very disclosure the retry needed. Everything
# above only PROBES; the mutation happens here, once the call is certain
# to proceed to the gate and the fetch.
#
# ONE deliberate exception, above: carry_forward_or_refuse revives
# before ITS refusal. That is correct -- a recovered screen must not sit
# orphaned on the shelf while the reply says it was recovered -- and it
# loses nothing, because that decision is driven by the prior PREP ROW,
# which is still in the store and re-derives the identical note on the
# retry. The re-sent-id path has no such source of truth.
#
# --- Jira image gate, BEAT 1 (QA_IMAGE_GATE_ENABLED) ------------------- #
# Placed AFTER the duplicate-prep guard (an already-open prep is the
# cheaper, more urgent signal) and BEFORE the fetch
#
# 2026-08-31 (F3): that ordering is still right, but it MASKED this gate
# -- a tester re-running a ticket saw only the duplicate warning and never
# the screens question, which then cost a further round trip after the
# proceed_anyway retry. Both duplicate clarifies now append
# _pending_image_gate_hint, whose condition MIRRORS the `if` below. Change
# one and you must change the other, or the hint promises a question that
# never comes (or stays silent about one that does). -- which is the whole
# point: the "I cannot read Jira images" disclosure used to be appended to a
# payload the host had ALREADY been told to generate from.
#
# REACHABILITY: handle_generate_test_cases re-routes into this handler in
# host mode, so both beats fire on qa_generate_test_cases too -- which is
# why that handler and that tool forward these same four arguments.
#
# PEEK, never pop. A Jira source's first prepare returns the fetch DIRECTIVE,
# so consuming the tray here would destroy the screens before the ticket
# arrived. _drop_captures runs only once a payload actually ships.
#
# The SECOND unconditional server-side call on this path (the first
# being the ambiguity classifier above): rtm.generate_acs, which fires
# whenever the ticket carried no parsed ACs and has no off switch of
# its own. Decided BEFORE prepare_generation because AC synthesis is
# prepare-side -- its output feeds rtm_hint and the RTM -- so it
# cannot be deferred to submit; what is deferred is the RESULT.
# Retained because it still decides whether the raw bytes are forwarded
# to the host as MCP image content (host_images / _img_job below). The
# two server-side vision calls it used to suppress -- the Jira
# ticket-image description and ui_extractor's Tier-3 fallback -- were
# DELETED on 2026-08-16 (P2-F1), so there is nothing left to suppress.
#
# Whether the AC job was actually SHIPPED, which is narrower than the
# flag: a ticket that carried real acceptance criteria needs no job at
# all (source_acs is non-empty and nothing was synthesized either way).
def _host_generation_mode() -> bool:
    """True when generation runs in host mode.

    Evening-ops repair: `llm` is NOT imported at module scope in this file,
    so it is imported here; a failure still surfaces in the caller's except
    as "Preparation failed"."""

    return llm.resolve_generation_mode() == "host"


def _revive_shelved(capture_ids: list | None) -> None:
    """Revive any shelved captures; the caller peeks the tray after this."""
    if capture_ids:
        capture.revive_captures(capture_ids)


def _beat1_clarify(menu: str, rendered_missing: str) -> str:
    """The BEAT 1 menu, with the rendered missing-captures note appended when due.

    The caller renders it, so `_render_missing` stays paired with its own
    `peek_captures` (tests/test_render_missing_pairing.py)."""
    if rendered_missing:
        return (
            menu
            + generate._BEAT1_MISSING_HEAD
            + rendered_missing
            + generate._BEAT1_MISSING_TAIL
        )
    return menu


async def _plan_nudge_if_due(args: PlanNudgeArgs, image_gate_ack: bool) -> str | None:
    """The plan nudge, or None when there is no plan or the gate is acked."""
    if args.plan and not image_gate_ack:
        return await generate.image_plan_nudge(args)
    return None


def _peek_captured(sent: list | None) -> tuple[list, list, list, str]:
    """Peek the capture tray; render the missing-captures note when due."""
    cap_images, cap_labels, cap_missing = capture.peek_captures(sent)
    missing_rendered = capture._render_missing(cap_missing, sent) if cap_missing else ""
    return cap_images, cap_labels, cap_missing, missing_rendered

"""Jira image gate: the refusal of a re-prepare that would lose the last prep's screens."""

from __future__ import annotations

import logging
from typing import NamedTuple

from config.settings_guard import never_raise
from handlers.image_gate_names import _clamped_count
from handlers.image_gate_refusal_parts import (
    _refusal_ago,
    _refusal_carries_line,
    _refusal_channels,
    _refusal_consequence,
    _refusal_counts,
    _refusal_label_block,
    _RefusalCounts,
    _RefusalIn,
)

logger = logging.getLogger("tools.mcp_handlers")


class _RefusalReq(NamedTuple):
    """The keyword arguments of the re-prepare image-loss refusal."""

    prep_id: str
    age_s: float
    captured: int
    attested: int
    labels: list
    recovered: int = 0
    carries_captured: int = 0
    carries_attested: int = 0
    shortfall: int = 0
    prior_captured: int | None = None
    prior_attested: int | None = None


_REFUSAL_OPTIONS = (
    "\n\nCall the SAME tool again with the SAME `feature_or_url` (and "
    "the SAME `jira_content_json` if you already fetched the ticket -- do "
    "NOT fetch it twice) plus ONE of:\n\n"
    "1. **The missing screens** -- re-run `qa_capture_screens` and pass "
    "the new `capture_ids`, and/or re-attach the screenshots and pass "
    "`attached_image_count=<how many>` (add `proceed_anyway=true` if this "
    "is also a deliberate restart of the open preparation).\n"
    "2. **Generate with what is available, deliberately** -- pass "
    "`image_carry_ack=true` (add `proceed_anyway=true` if this is also a "
    "deliberate restart of the open preparation). Ask the user first: "
    "the cases will not reflect anything that exists only in those "
    "screens, and the reply will say so.\n\n"
    "Nothing has been prepared yet, so this costs no generation."
)


_REFUSAL_FALLBACK = (
    "\u26d4 A recent preparation for this exact source was grounded on "
    "screens that this call does not carry. Re-send them, or pass "
    "`image_carry_ack=true` to generate from the ticket text alone "
    "(`proceed_anyway=true` does not dismiss this)."
)


class _RefusalCall(NamedTuple):
    """The caller-facing inputs of one image-loss refusal."""

    prep_id: str
    age_s: float
    labels: list
    carries: tuple


def _refusal_recovered_line(counts: _RefusalCounts) -> str:
    """Return the sentence naming how many missing screens were recovered."""
    # "of the MISSING screen(s)", same review: now that the channels clause
    # counts the prior TOTALS, a bare "of them" would read as a recovery out
    # of that total rather than out of the shortfall.
    return (
        f" I recovered {counts.recovered_cap} of the missing screen(s) from that "
        f"preparation, so {counts.short_total} would still be missing."
        if counts.recovered_cap
        else ""
    )


def _refusal_text(call: _RefusalCall, counts: _RefusalCounts) -> str:
    """Assemble the image-loss refusal from its computed parts."""
    carries_line, has_images = _refusal_carries_line(call.carries)
    consequence = _refusal_consequence(has_images or bool(counts.recovered_cap))
    return (
        f"## \u26d4 This would generate WITHOUT {counts.short_total} of the screens "
        "the last preparation had\n\n"
        f"A preparation for this exact source (`{call.prep_id}`, started "
        f"{_refusal_ago(call.age_s)}) was grounded on "
        + _refusal_channels(counts)
        + "."
        + carries_line
        + _refusal_recovered_line(counts)
        + consequence
        + _refusal_label_block(call.labels)
        + _REFUSAL_OPTIONS
    )


def _render_refusal(a: dict) -> str:
    """Render the refusal from the keyword arguments of the public entry point."""
    priors = (a["prior_captured"], a["prior_attested"])
    counts = _refusal_counts(
        _RefusalIn(a["captured"], a["attested"], a["recovered"], a["shortfall"], priors)
    )
    carries = (
        _clamped_count(a["carries_captured"], hi=99),
        _clamped_count(a["carries_attested"], hi=99),
    )
    call = _RefusalCall(a["prep_id"], a["age_s"], a["labels"], carries)
    return _refusal_text(call, counts)


# REFUSE a re-prepare that would generate without screens the last one had.
#
# 2026-08-09, from a live run: a prep carrying 2 captured device screens was
# followed 3 minutes later by a second prep for the SAME source with none, and
# all 8 categories were written from the imageless one. The generic
# duplicate-prep warning is dismissed by ``proceed_anyway=true`` -- which that
# host model sent -- so this refusal takes its OWN, SPECIFIC ack
# (``image_carry_ack=true``), exactly like ``volume_floor_ack`` on the submit
# side, and NAMES both flags together so a deliberate restart still costs one
# call rather than two refusals in a row.
#
# Describes the SHORTFALL, not the whole prior prep: anything this server
# already recovered off the carry-forward shelf is counted and excluded, so a
# partial recovery never reads as a total loss. The labels are the tester's own
# words -- UNTRUSTED text -- so they ride inside wrap_untrusted. The except
# returns a SHORTER refusal rather than "": a disclosure that cannot be
# rendered must never become a silent proceed.
def _reprep_image_loss_refusal(req: _RefusalReq) -> str:
    """Refuse a re-prepare that would generate without screens the last one had."""
    with never_raise(logger, "_reprep_image_loss_refusal failed", exc_info=True):
        return _render_refusal(req._asdict())
    return _REFUSAL_FALLBACK

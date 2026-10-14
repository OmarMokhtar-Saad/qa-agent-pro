"""Jira image gate: the counts and sentence parts of the image-loss refusal."""

from __future__ import annotations

from typing import NamedTuple

from handlers.image_gate_names import _clamped_count
from tools.untrusted import wrap_untrusted


class _RefusalCounts(NamedTuple):
    """The clamped counts behind one image-loss refusal (see UNITS below)."""

    recovered_cap: int
    prior_cap: int
    prior_att: int
    short_total: int


class _RefusalIn(NamedTuple):
    """The raw counts handed to one image-loss refusal, before clamping."""

    captured: int
    attested: int
    recovered: int
    shortfall: int
    priors: tuple


def _refusal_priors(priors: tuple, gap_cap: int, gap_att: int) -> tuple[int, int]:
    """Return the prior prep's clamped totals, falling back to the gaps."""
    prior_captured, prior_attested = priors
    return (
        _clamped_count(gap_cap if prior_captured is None else prior_captured, hi=99),
        _clamped_count(gap_att if prior_attested is None else prior_attested, hi=99),
    )


# UNITS (2026-08-09, adversarial review of 2dcdc73). Every count here
# carries what it MEASURES in its own name, because the five bugs that
# review found were all ONE variable silently changing meaning between
# the arithmetic and the prose:
#   gap_*      -- screens the prior prep had that this call does NOT
#   prior_*    -- what the prior prep was grounded on, in TOTAL
#   carries_*  -- what THIS call carries
#   short_*    -- the residual after recovery and any surplus credit
# `captured`/`attested` arrive as GAPS (kept for the existing call
# sites); the prior TOTALS arrive separately and fall back to the gaps
# for a direct caller that only has those.
# 2026-08-09 (review C1): the caller may have credited a channel SURPLUS
# (screens this call carries beyond what the prior prep had on that
# channel) that this helper cannot see, so it passes the authoritative
# shortfall in. 0 means "work it out", which is the original behaviour
# and what the direct-call tests exercise.
def _refusal_counts(inp: _RefusalIn) -> _RefusalCounts:
    """Clamp and combine the counts the refusal arithmetic needs."""
    gap_cap = _clamped_count(inp.captured, hi=99)
    gap_att = _clamped_count(inp.attested, hi=99)
    recovered_cap = _clamped_count(inp.recovered, hi=99)
    prior_cap, prior_att = _refusal_priors(inp.priors, gap_cap, gap_att)
    short_total = _clamped_count(inp.shortfall, hi=99) or max(
        1, gap_cap + gap_att - recovered_cap
    )
    return _RefusalCounts(recovered_cap, prior_cap, prior_att, short_total)


def _refusal_ago(age_s: float) -> str:
    """How long ago the prior preparation started, in words."""
    try:
        mins = max(0, int(float(age_s or 0) / 60))
    except (TypeError, ValueError, OverflowError):
        mins = 0
    return f"{mins} minute(s) ago" if mins else "less than a minute ago"


def _refusal_label_block(labels: list) -> str:
    """The prior preparation's screen labels, wrapped as untrusted text."""
    named = [str(x).strip() for x in list(labels or [])[:8] if str(x).strip()]
    if not named:
        return ""
    return "\n\nThe previous preparation named its screens:\n\n" + wrap_untrusted(
        "prior_screen_labels", "\n".join(named), limit=800
    )


def _refusal_channels(counts: _RefusalCounts) -> str:
    """What the prior preparation was grounded on, as one phrase."""
    # 2026-08-09 (review M2): the "was grounded on" clause describes the
    # PRIOR PREP, so it renders prior TOTALS. It used to render the GAPS
    # handed in for the headline, so a prep grounded on 2 + 2 was reported
    # as having had 1 + 1 -- the shortfall arithmetic leaking into a
    # sentence about a different quantity.
    channels = []
    if counts.prior_cap:
        channels.append(f"{counts.prior_cap} device screen(s) captured on this server")
    if counts.prior_att:
        channels.append(f"{counts.prior_att} screenshot(s) you attached to the chat")
    if not channels:
        # A degenerate direct call must not render "was grounded on .".
        channels.append("screens it did not record")
    return " and ".join(channels)


def _refusal_carries_line(carries: tuple) -> tuple[str, bool]:
    """What THIS call carries, and whether it carries any image."""
    # 2026-08-09 (review C1): this used to hardcode "THIS call carries no
    # images at all", which became FALSE the moment the precondition went
    # per-channel -- a call that re-captures two device screens but drops the
    # chat attachments carries plenty of images, just not the missing ones.
    # Say what the call actually carries, so the tester can see the
    # substitution the server saw.
    carries_cap, carries_att = carries
    has = []
    if carries_cap:
        has.append(f"{carries_cap} captured device screen(s)")
    if carries_att:
        has.append(f"{carries_att} attached screenshot(s)")
    if not has:
        return " THIS call carries no images at all.", False
    return " THIS call carries " + " and ".join(
        has
    ) + ", which does not cover them.", True


def _refusal_consequence(has_images: bool) -> str:
    """The sentence naming what generating now would do."""
    # 2026-08-09 (review M2): the TEXT-alone claim is CONDITIONAL now. It
    # sat two clauses after the carries line had just listed the images this
    # call DOES carry, so the refusal contradicted itself on every partial
    # substitution.
    if has_images:
        return (
            " Generating now writes those cases WITHOUT them -- that is the "
            "silent regression this guard exists to prevent, and "
            "`proceed_anyway=true` does NOT dismiss it."
        )
    return (
        " Generating now writes those cases from the ticket TEXT alone "
        "-- that is the silent regression this guard exists to prevent, and "
        "`proceed_anyway=true` does NOT dismiss it."
    )

"""The notice of a packet block and its last-resort fallback."""

from __future__ import annotations

from tools.mobile import perception_limits


# The notice for a given outcome -- recomputed, never patched.
#
# It has to be a function of the count because the count is what the
# fixed point in `to_prompt_block` changes: drop a line and both the shown
# number and the not-carried number move, so a notice composed once and
# reused is a notice about a block that no longer exists.
# ``drops`` is ``(inside, outside, unknown)``.
#
# ``counts`` is ``(inside, outside, unknown, shown_count, too_big)``.
def _block_notice(content: dict, counts: tuple) -> str:
    """The notice for a given outcome -- recomputed, never patched."""
    inside, outside, unknown, shown_count, too_big = counts
    if inside + outside + unknown + too_big:
        return _missing_note(counts)
    if content.get("truncated"):
        return _exceeded_note(shown_count)
    return ""


def _missing_note(counts: tuple) -> str:
    """The "N shown; M not carried" note, with whatever split is known."""
    inside, outside, unknown, shown_count, too_big = counts
    missing = inside + outside + unknown + too_big
    note = "...[%d elements shown; %d not carried" % (shown_count, missing)
    if inside:
        note += _inside_clause(inside)
    if outside:
        note += ", %d off the display" % outside
    if unknown:
        note += ", %d with no display to place them" % unknown
    if too_big:
        note += _too_big_clause(too_big)
    return note + "]"


# The one a planner must act on: the screen it can SEE is
# incomplete, so the next step should scroll, not assume.
def _inside_clause(inside: int) -> str:
    """The clause for elements lost on the visible screen."""
    return ", %d of them on this screen" % inside


# Deliberately NOT split by side of the panel: the split for
# the budget drops is known, this one is not, and claiming a
# precision we do not have is the defect this message has
# already had twice.
def _too_big_clause(too_big: int) -> str:
    """The clause for elements beyond the size of the message."""
    return (
        ", %d beyond the size of this message (some of those may "
        "be on this screen too)" % too_big
    )


# `truncated` still means "the dump offered more than
# MAX_ELEMENTS" -- it gates `screen_hit`'s press refusal and is
# deliberately unchanged -- so it can be true while the headroom
# carried everything. Say that, rather than claim a loss that
# did not happen.
def _exceeded_note(shown_count: int) -> str:
    """The note for a dump that exceeded the cap with nothing dropped."""
    return (
        "...[%d elements shown; this screen exceeded the usual cap "
        "but nothing was dropped]" % shown_count
    )


# LAST RESORT: one header line that still will not fit. Returning the
# text measured as too long let `wrap_untrusted` slice it and take
# the notice with it -- 5 elements and no warning at all, which is
# the very failure the header drop above was added to prevent. So
# make room for the notice explicitly and compose what is returned.
def _last_resort_text(head_base: list[str], notice_args: tuple, shown: int) -> str:
    """One header line that still will not fit: make room for the notice."""
    content, drops = notice_args
    note = _block_notice(content, (*drops, 0, shown))
    keep = perception_limits.MAX_BLOCK_CHARS - len(note) - 1
    head_base = [head_base[0][:keep]] if keep > 0 else []
    return "\n".join(head_base + ([note] if note else []))

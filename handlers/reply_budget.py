"""Finalize-reply budget: summary allowance and assembly (moved from handlers._reply)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers.reply_consts import (
    _REPLY_CAP,
    _SUMMARY_CAP,
    _SUMMARY_FLOOR,
    _TRUNC_RESERVE,
)
from handlers.reply_section import (
    ReplySection,
    _omission_marker,
    _sections_to_drop,
    _unbounded_reply,
)

logger = logging.getLogger("tools.mcp_handlers")


def _overhead(sections: list[ReplySection], header_len: int) -> int:
    """Chars of the reply that are not the summary."""
    return sum(len(s.text) for s in sections) + max(header_len, 0) + _TRUNC_RESERVE


def summary_budget(sections: list[ReplySection], header_len: int) -> int:
    """How many chars of `summary` fit, given everything else in the reply.

    *sections* must be EVERY section of the reply except the one the summary
    sits in -- including the ones appended after it, which the round-2 review
    caught this missing: `cap_note` is added at the assemble call and was never
    counted, so the reply over-ran the cap by its length. Pass the whole list;
    the only thing left out is the suite block.

    Clamped into [_SUMMARY_FLOOR, _SUMMARY_CAP], so this can only ever be
    STRICTER than the cap that shipped, never looser: a reply with few notes
    keeps today's exact 4000-char allowance and stays byte-identical.
    """
    with never_raise(
        logger, "summary budget failed - using the static cap", exc_info=True
    ):
        overhead = _overhead(sections, header_len)
        return max(_SUMMARY_FLOOR, min(_SUMMARY_CAP, _REPLY_CAP - overhead))
    return _SUMMARY_CAP


def _assemble_over(sections: list[ReplySection], joined: str, cap: int) -> str:
    """The over-budget branch of `assemble_finalize_reply`."""
    dropped = _sections_to_drop(sections, len(joined), cap)
    if not dropped:
        return joined
    gone = set(dropped)
    body = "".join(s.text for i, s in enumerate(sections) if i not in gone)
    out = body + _omission_marker([sections[i].name for i in sorted(gone)])
    # Dropping may never make the reply LONGER than leaving it alone. The
    # marker costs ~300 chars, so a droppable set smaller than that is a net
    # loss -- and the tester would have paid a lost section for nothing.
    # Round-2 review, the tail of MAJOR 1.
    return out if len(out) < len(joined) else joined


# UNDER budget the result is byte-identical to ``"".join(s.text ...)``. That
# fast path is the point of the design: every healthy run keeps today's exact
# reply, so every assertion pinned on it stays true.
#
# OVER budget, trimmable sections are dropped WHOLE and in STRICT priority
# order -- the loop stops at the first section it can keep, so a long
# high-priority section can never lose its place to a short low-priority one.
# The names of the dropped sections are disclosed in a trailing marker whose
# length is reserved BEFORE the decision, because disclosure may not be the
# thing that pushes the reply back over the bound.
#
# A protected section is never dropped and never truncated, so the bound is
# honest rather than absolute: if the protected band alone exceeds *cap*, the
# reply is that band plus the marker. Bookkeeping is keyed on INDEX, not on
# name, so a future section that reuses a name cannot silently duplicate or
# delete another section's text.
def assemble_finalize_reply(sections: list[ReplySection], cap: int = _REPLY_CAP) -> str:
    """Join *sections* into the submit reply, bounded by *cap*. Never raises."""
    with never_raise(
        logger,
        "finalize reply budget failed - returning the reply unbounded",
        level=logging.WARNING,
        exc_info=True,
    ):
        joined = "".join(s.text for s in sections)
        if len(joined) <= cap:
            return joined
        return _assemble_over(sections, joined, cap)
    return _unbounded_reply(sections)

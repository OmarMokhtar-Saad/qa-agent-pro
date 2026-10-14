"""Rendering leaf of handover_cost: Handover -> the tester-facing block."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.hcost_core import (
    _CHARS_PER_TOKEN_OPTIMISTIC,
    _CHARS_PER_TOKEN_PESSIMISTIC,
    Handover,
    token_band,
)
from tools.hcost_measure import measure

logger = logging.getLogger("tools.handover_cost")

_HEAD = (
    "### What this will cost YOUR chat model\n"
    "\n"
    "This server runs no model of its own: YOUR chat model generates "
    "every test case, so this work lands on YOUR subscription and this "
    "server never sees one of those tokens. Measured from the payload "
    "it just handed you:"
)
_NO_FANOUT = (
    "### What this will cost YOUR chat model\n\n"
    "This payload carries no category fan-out, so there is nothing "
    "to size."
)


def _s(n: int) -> str:
    """Plural suffix for ``n``. A line reading "1 category generations" looks
    like a bug in the measurement, which is the last impression a number a
    tester is being asked to act on should give."""
    return "" if n == 1 else "s"


def _images_line(h) -> str:
    if not h.image_count:
        return "- **Images riding along: none.**"
    return (
        f"- **Images riding along: {h.image_count}** "
        f"({h.image_bytes:,} raw bytes). Image tokens are NOT in the "
        "estimate below -- every model tiles images differently."
    )


def _estimate_line(low: int, high: int) -> str:
    return (
        f"- **ROUGH input-token estimate: {low:,}-{high:,} tokens.** This "
        f"is division, not measurement: character count divided by "
        f"{_CHARS_PER_TOKEN_OPTIMISTIC} for the low end and "
        f"{_CHARS_PER_TOKEN_PESSIMISTIC} for the high end. Your model's "
        "own tokenizer decides the real figure; non-Latin scripts such as "
        "Arabic land at or ABOVE the high end. OUTPUT tokens are not "
        "counted at all -- the server cannot know how much your model "
        "writes -- and there is no price here, because the server does "
        "not know which model you run or what you pay for it."
    )


def _turns_line(h: Handover) -> str:
    return (
        f"- **Model turns: about {h.model_turns}** -- {h.categories} "
        f"category generation{_s(h.categories)} plus {h.step_zero_jobs} "
        f"step-0 job{_s(h.step_zero_jobs)}. One generation per category "
        "is the intended flow; a client that batches them will make "
        "fewer, larger calls."
    )


def _shared_line(h: Handover, per_cat_instruction: int) -> str:
    return (
        f"- **Shared context, re-sent with every category: "
        f"{h.shared_chars:,} chars** (prompt + ticket text + response "
        f"schema), plus about {per_cat_instruction:,} chars of "
        "category-specific instruction. The shared part is NOT sent once "
        "-- it goes again with each category."
    )


def _total_line(h: Handover, per_call: tuple[int, int]) -> str:
    return (
        f"- **Total input handed over: {h.total_input_chars:,} chars** "
        f"across {h.categories} call{_s(h.categories)} "
        f"(~{per_call[0]:,}-{per_call[1]:,} tokens per call). This "
        f"payload is {h.payload_bytes:,} bytes on the wire."
    )


def _render_handover(h: Handover) -> str:
    """The full disclosure block for a measurement that has categories."""
    low, high = token_band(h.total_input_chars)
    per_cat = h.instruction_chars // h.categories
    per_call = token_band(h.shared_chars + per_cat)
    return "\n".join(
        [
            _HEAD,
            "",
            _turns_line(h),
            _shared_line(h, per_cat),
            _total_line(h, per_call),
            _images_line(h),
            _estimate_line(low, high),
        ]
    )


# The tester-facing block for one prepare reply.
#
# Never empty when there is a payload to measure -- an always-empty
# disclosure is how the previous cost feature died. Never raises.
#
def handover_line(
    payload: object, *, image_count: int = 0, image_bytes: int = 0
) -> str:
    """The tester-facing block for one prepare reply; never raises."""
    # a disclosure never breaks a prepare
    with never_raise(logger, "rendering the handover cost failed", exc_info=True):
        h = measure(payload, image_count=image_count, image_bytes=image_bytes)
        if not h.categories:
            return _NO_FANOUT
        return _render_handover(h)
    return ""

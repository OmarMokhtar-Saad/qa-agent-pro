"""What this server HANDS OVER to the tester's own chat model, measured once,
at the moment of handover.

WHY THIS IS NOT THE DELETED TOKEN METER (read before changing it). The old
``tools/token_meter.py`` accumulated per-phase usage and rendered a ``$``
estimate on the submit reply. It was deleted on 2026-08-16 (dead-code deletion
P2-G2a) for a reason that is a design lesson rather than a bug: generation went
chat-only on 2026-08-12, so nothing ever called ``note()`` again and
``summary_line()`` returned ``""`` on every single submit. It was flagless,
always ON, and always EMPTY, and no tester ever saw a cost line.

The shape difference that keeps this module honest:

* It ACCUMULATES NOTHING. There is no ``note()``, no store, no session total.
  Every number is computed by ``measure()`` from the payload object being
  handed over in that same call, so it cannot go stale and it cannot go empty
  while a payload exists.
* It measures the HANDOVER, not the spend. The server can see exactly how many
  characters and images it puts on the wire. It cannot see a token, cannot see
  which model the tester runs, and cannot see the output length -- so none of
  those are reported, and ``handover_line`` says so out loud.
* There is NO currency figure and NO per-model table. A ``$`` here would be a
  fabricated number, which is precisely what got the last feature deleted.

Pure and synchronous: no I/O, no settings read, no model call. Never raises --
a cost disclosure may not break a prepare.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The two ends of the ROUGH token guess, in CHARACTERS PER TOKEN. They are
# deliberately a WIDE band rather than one ratio, because a single number gets
# over-trusted and this is arithmetic on character counts, not a tokenizer:
#
#   * 4 chars/token  -- the optimistic end. Roughly right for English prose and
#     for the repetitive JSON/schema text that dominates this payload.
#   * 2 chars/token  -- the pessimistic end. Covers dense punctuation, ids and
#     mixed-script text.
#
# Non-Latin scripts (Arabic ticket text is common here) can exceed even the
# pessimistic end, so ``handover_line`` states that instead of pretending the
# band is a bound.
_CHARS_PER_TOKEN_OPTIMISTIC = 4
_CHARS_PER_TOKEN_PESSIMISTIC = 2


@dataclass(frozen=True)
class Handover:
    """One measurement of one prepare payload. All counts, no estimates."""

    categories: int = 0
    step_zero_jobs: int = 0
    model_turns: int = 0
    shared_chars: int = 0
    instruction_chars: int = 0
    total_input_chars: int = 0
    payload_bytes: int = 0
    image_count: int = 0
    image_bytes: int = 0


def payload_bytes(obj: object) -> int:
    """Byte size of ``obj`` as it goes on the wire.

    Same two branches, and the same ``ensure_ascii=False`` UTF-8 encode, as
    ``mcp_handlers._submission_bytes`` uses for an inbound submission: a size
    must be measured in the representation it is denominated in, and a
    re-escaping ``json.dumps`` over an already-serialised string once made a
    57,129-byte payload measure 64,297. Returns 0 for anything unmeasurable.
    """
    try:
        if isinstance(obj, str):
            return len(obj.encode("utf-8", "ignore"))
        return len(json.dumps(obj, ensure_ascii=False).encode("utf-8", "ignore"))
    except (TypeError, ValueError, RecursionError):
        return 0


# The payload keys whose text is re-sent with EVERY category call. This is the
# whole point of the measurement: the tester's client does not send the shared
# context once, it sends it again for each category in the fan-out, so an
# 8-way fan-out over a 23k-char context is ~184k chars of input, not 23k.
_SHARED_TEXT_KEYS = (
    "system_prompt",
    "user_context",
    "untrusted_data_notice",
    "image_context",
)


def _text_chars(value: object) -> int:
    """Character count of a payload field: strings as-is, anything else as the
    JSON the client will actually receive (the response schema is a dict)."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError, RecursionError):
        return 0


def measure(
    payload: object,
    *,
    image_count: int = 0,
    image_bytes: int = 0,
) -> Handover:
    """Measure one prepare payload.

    ``image_count`` / ``image_bytes`` describe the images that will ACTUALLY
    ride on the reply -- the caller passes the POST-budget figures, because
    only the MCP assembly layer knows which attachments survive the per-result
    byte budget and the count cap. Passing the pre-budget totals would
    over-report a handover that never happens.

    Never raises: on any internal error it returns an all-zero Handover, and
    ``handover_line`` then says the payload could not be sized rather than
    printing a wrong number.
    """
    try:
        data = payload if isinstance(payload, dict) else {}
        categories = [c for c in (data.get("categories") or []) if isinstance(c, dict)]
        jobs = [j for j in (data.get("jobs_to_run") or []) if isinstance(j, dict)]
        shared = sum(_text_chars(data.get(k)) for k in _SHARED_TEXT_KEYS)
        shared += _text_chars(data.get("response_schema"))
        instruction = sum(_text_chars(c.get("instruction")) for c in categories)
        n = len(categories)
        return Handover(
            categories=n,
            step_zero_jobs=len(jobs),
            model_turns=n + len(jobs),
            shared_chars=shared,
            instruction_chars=instruction,
            total_input_chars=shared * n + instruction,
            payload_bytes=payload_bytes(payload),
            image_count=max(0, int(image_count or 0)),
            image_bytes=max(0, int(image_bytes or 0)),
        )
    except Exception:  # pragma: no cover - a measurement never breaks a prepare
        logger.debug("measuring the handover failed", exc_info=True)
        return Handover()


def _s(n: int) -> str:
    """Plural suffix for ``n``. A line reading "1 category generations" looks
    like a bug in the measurement, which is the last impression a number a
    tester is being asked to act on should give."""
    return "" if n == 1 else "s"


def token_band(chars: int) -> tuple[int, int]:
    """``(low, high)`` ROUGH input-token guess for ``chars`` characters.

    Division, not tokenization. A band is returned rather than a midpoint so no
    caller can render a single authoritative-looking number.
    """
    try:
        c = max(0, int(chars or 0))
    except (TypeError, ValueError):
        return (0, 0)
    return (c // _CHARS_PER_TOKEN_OPTIMISTIC, c // _CHARS_PER_TOKEN_PESSIMISTIC)


def handover_line(
    payload: object,
    *,
    image_count: int = 0,
    image_bytes: int = 0,
) -> str:
    """The tester-facing block for one prepare reply.

    Never empty when there is a payload to measure -- an always-empty
    disclosure is how the previous cost feature died. Never raises.
    """
    try:
        h = measure(payload, image_count=image_count, image_bytes=image_bytes)
        if not h.categories:
            return (
                "### What this will cost YOUR chat model\n\n"
                "This payload carries no category fan-out, so there is nothing "
                "to size."
            )
        low, high = token_band(h.total_input_chars)
        per_cat_instruction = h.instruction_chars // h.categories
        per_call_low, per_call_high = token_band(h.shared_chars + per_cat_instruction)
        lines = [
            "### What this will cost YOUR chat model",
            "",
            "This server runs no model of its own: YOUR chat model generates "
            "every test case, so this work lands on YOUR subscription and this "
            "server never sees one of those tokens. Measured from the payload "
            "it just handed you:",
            "",
            f"- **Model turns: about {h.model_turns}** -- {h.categories} "
            f"category generation{_s(h.categories)} plus {h.step_zero_jobs} "
            f"step-0 job{_s(h.step_zero_jobs)}. One generation per category "
            "is the intended flow; a client that batches them will make "
            "fewer, larger calls.",
            f"- **Shared context, re-sent with every category: "
            f"{h.shared_chars:,} chars** (prompt + ticket text + response "
            f"schema), plus about {per_cat_instruction:,} chars of "
            "category-specific instruction. The shared part is NOT sent once "
            "-- it goes again with each category.",
            f"- **Total input handed over: {h.total_input_chars:,} chars** "
            f"across {h.categories} call{_s(h.categories)} "
            f"(~{per_call_low:,}-{per_call_high:,} tokens per call). This "
            f"payload is {h.payload_bytes:,} bytes on the wire.",
        ]
        if h.image_count:
            lines.append(
                f"- **Images riding along: {h.image_count}** "
                f"({h.image_bytes:,} raw bytes). Image tokens are NOT in the "
                "estimate below -- every model tiles images differently."
            )
        else:
            lines.append("- **Images riding along: none.**")
        lines.append(
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
        return "\n".join(lines)
    except Exception:  # pragma: no cover - a disclosure never breaks a prepare
        logger.debug("rendering the handover cost failed", exc_info=True)
        return ""

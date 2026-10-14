"""Constants, the Handover record and char/token arithmetic for handover_cost."""

from __future__ import annotations

import json
from dataclasses import dataclass

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


def token_band(chars: int) -> tuple[int, int]:
    """``(low, high)`` ROUGH input-token guess for ``chars`` characters.

    Division, not tokenization. A band is returned rather than a midpoint so no
    caller can render a single authoritative-looking number.
    """
    try:
        c = max(0, int(chars or 0))
    except (TypeError, ValueError, OverflowError):
        return (0, 0)
    return (c // _CHARS_PER_TOKEN_OPTIMISTIC, c // _CHARS_PER_TOKEN_PESSIMISTIC)

"""Is a quoted span asserted product copy? (leaf of oracle_grounding)."""

from __future__ import annotations

import re

# How many real WORDS a span must carry before it is treated as asserted copy.
# This is the filter that keeps identifiers and bare values out: 'CH-100452'
# (a cardholder id the case itself seeds), 'SAR 5,000', '1,500', 'abc' and
# 'WrongPass1' each carry at most one, while 'Enter a valid amount' carries
# three. Its cost is a MISS on genuine one-word copy such as 'Frozen', which is
# the cheap direction for an advisory -- and every one-word span in the
# 2026-08-16 run was grounded anyway.
_MIN_ALPHA_TOKENS = 2

_ALPHA_TOKEN_RE = re.compile(r"[A-Za-z؀-ۿ]{2,}")

#: A quoted span with NO whitespace that carries an underscore, or a hyphen
#: alongside a digit, is a machine IDENTIFIER -- an event-type constant, an API
#: enum, a test-case id -- not copy the product renders.
#:
#: MEASURED on the v1.61.1 live run against TICKET-10884: 3 of the 10 spans this
#: checker flagged were `'APT-NEG-001'` (a test-case id),
#: `'APPOINTMENT_ARRIVAL_SURVEY'` (an event type) and `'CHANNEL_DISABLED'` (an
#: API enum). `_ALPHA_TOKEN_RE` reads APT/NEG and CHANNEL/DISABLED as two alpha
#: tokens each, so all three cleared `_MIN_ALPHA_TOKENS`.
#:
#: This is not a cosmetic miss. The advice attached to a flagged span is "assert
#: what the message MUST SAY instead of its exact text, or get the real copy
#: into the ticket" -- meaningless for an enum constant, and it tells the tester
#: to LOOSEN an assertion that is correctly exact. A false positive here costs
#: more than a miss, which is why the two rules below are deliberately narrow
#: and biased toward keeping real copy: 'SAVE CHANGES' has whitespace, and
#: 'SIGN-IN' is hyphenated WITHOUT a digit, so both still reach the checker.
#:
#: Dotted i18n keys (`errors.field.required`) are a real third class and are NOT
#: excluded here: no run has produced one, and an untested rule risks a false
#: negative on real copy for no measured gain.


def _looks_like_identifier(span: str) -> bool:
    """True for a machine identifier that a UI never renders. Never raises."""
    if not span or any(ch.isspace() for ch in span):
        return False
    if "_" in span:
        return True
    return "-" in span and any(ch.isdigit() for ch in span)


# The only trailing punctuation a real UI string may end on. Anything else at
# either end means the "span" is prose caught between two unrelated quotes --
# the naive regex produced ', new_value ' and ' and action ' out of one
# expected result's ordinary commas, and both must stay silent.
_SENTENCE_TAIL = ".!?)"


def _is_asserted_ui_string(span: str) -> bool:
    """True when a quoted span is plausibly copy the product must render."""
    if not span or not span[0].isalnum():
        return False
    if not span[-1].isalnum() and span[-1] not in _SENTENCE_TAIL:
        return False
    if _looks_like_identifier(span):
        return False
    return len(_ALPHA_TOKEN_RE.findall(span)) >= _MIN_ALPHA_TOKENS

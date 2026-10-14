"""Quoted-span extraction (leaf of oracle_grounding)."""

from __future__ import annotations

import re

# A quoted span longer than this is a paragraph, not a UI string.
_MIN_SPAN_CHARS = 6
_MAX_SPAN_CHARS = 120

# Straight and curly, single and double. The generator writes straight singles;
# a pasted description routinely uses curly ones, and normalizing BOTH sides is
# what lets a curly-quoted promise ground a straight-quoted assertion.
# The single-quote alternatives are flanked by (?<![A-Za-z0-9]) / (?![A-Za-z0-9])
# because a straight or curly single quote is ALSO an apostrophe. Without the
# flanks the scan matched between the apostrophes of "the server's UTC date
# rather than the device's local date" and reported the fragment
# "s UTC date rather than the device" as invented UI copy -- one of the five
# false positives measured on the 2026-08-30 run (F7). A real quoted UI string
# is never preceded or followed immediately by a word character; a possessive
# always is. The DOUBLE-quote alternatives are deliberately left unflanked:
# they carry no apostrophe ambiguity and adding the lookarounds there could
# only lose real copy.
_SPAN_LEN = f"{_MIN_SPAN_CHARS},{_MAX_SPAN_CHARS}"
_QUOTED_SPAN_RE = re.compile(
    rf"(?<![A-Za-z0-9])'([^'\n]{{{_SPAN_LEN}}})'(?![A-Za-z0-9])"
    rf"|\"([^\"\n]{{{_SPAN_LEN}}})\""
    rf"|(?<![A-Za-z0-9])‘([^’\n]{{{_SPAN_LEN}}})’(?![A-Za-z0-9])"
    rf"|“([^”\n]{{{_SPAN_LEN}}})”"
)


def _quoted_spans(text: str | None) -> list[str]:
    """Every quoted span in ``text``, in order, whichever quote style is used."""
    out: list[str] = []
    for match in _QUOTED_SPAN_RE.finditer(text or ""):
        span = next((group for group in match.groups() if group is not None), None)
        if span:
            out.append(span)
    return out

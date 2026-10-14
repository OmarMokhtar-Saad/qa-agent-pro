"""Arabic script detection and bidi helpers for the EN/AR bilingual rule pack."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("tools.bilingual")

# --- Arabic script detection / bidi helpers ---------------------------------

_ARABIC_RANGES = (
    "؀-ۿ"  # Arabic
    "ݐ-ݿ"  # Arabic Supplement
    "ࢠ-ࣿ"  # Arabic Extended-A
    "ﭐ-﷿"  # Arabic Presentation Forms-A
    "ﹰ-﻿"  # Arabic Presentation Forms-B
)
_AR_CHAR_RE = re.compile("[" + _ARABIC_RANGES + "]")
# An Arabic "run": one or more Arabic words, allowing NEUTRAL characters
# (spaces, Arabic/Latin punctuation, digits) between two Arabic words, so a
# whole sentence is one directional run instead of many.
_AR_RUN_RE = re.compile(
    "["
    + _ARABIC_RANGES
    + "]+(?:[\\s0-9،؛؟٪-٭.,!?:;()\\[\\]/'\"\\-]+["
    + _ARABIC_RANGES
    + "]+)*"
)
_LATIN_RE = re.compile(r"[A-Za-z]")

RLM = "‏"  # RIGHT-TO-LEFT MARK
LRM = "‎"  # LEFT-TO-RIGHT MARK

# A cell whose letters are at least this fraction Arabic is treated as an
# Arabic-base cell (reading order right-to-left). Below it the cell keeps a
# left-to-right base and only the Arabic runs are isolated.
RTL_MAJORITY_RATIO = 0.5


def contains_arabic(text: str) -> bool:
    """True when *text* contains at least one Arabic-script character."""
    try:
        return bool(_AR_CHAR_RE.search(text or ""))
    except TypeError:  # pragma: no cover - defensive
        return False


def arabic_ratio(text: str) -> float:
    """Share of the LETTERS in *text* that are Arabic (0.0 - 1.0).

    Digits, punctuation and whitespace are ignored so a mostly-Arabic sentence
    containing Latin product codes still scores high. Never raises.
    """
    try:
        s = text or ""
        arabic = len(_AR_CHAR_RE.findall(s))
        latin = len(_LATIN_RE.findall(s))
        total = arabic + latin
        return (arabic / total) if total else 0.0
    except TypeError:  # pragma: no cover - defensive
        return 0.0


def is_rtl_cell(text: str) -> bool:
    """True when a spreadsheet cell should get reading_order=2 (right-to-left)."""
    return contains_arabic(text) and arabic_ratio(text) >= RTL_MAJORITY_RATIO


# Wrap every Arabic run in RLM ... LRM.
#
# The Unicode Bidirectional Algorithm reorders neutral characters (quotes,
# parentheses, colons) according to the surrounding direction, so an Arabic
# string quoted inside an English sentence -- ``AR: "..."`` -- renders with its
# closing quote in the wrong place. Bracketing each Arabic run with
# RIGHT-TO-LEFT MARK / LEFT-TO-RIGHT MARK pins the boundaries.
#
# A no-op for text with no Arabic (returned unchanged, byte for byte) and
# idempotent (already-isolated text is returned unchanged). Never raises.
def bidi_isolate(text: str) -> str:
    try:
        s = text or ""
        if not _AR_CHAR_RE.search(s):
            return s
        if RLM in s:
            return s  # already isolated - never double-wrap
        return _AR_RUN_RE.sub(lambda m: RLM + m.group(0) + LRM, s)
    except TypeError:  # pragma: no cover - defensive
        logger.debug("bidi_isolate failed - returning text unchanged", exc_info=True)
        return text or ""

"""Label folding for text matching (no imports from tools.mobile).

``fold`` is the EXACT-match fold: NFKC (so Arabic presentation forms, full-width
and ligature forms equal their base letters), whitespace collapse, strip, lower.
It is never applied to resource ids. ``skeleton`` is looser (Persian/Arabic
letter variants, tatweel, harakat dropped) and serves hints only: a label that
matches only by skeleton is named to the host, never acted on.
"""

from __future__ import annotations

import re
import unicodedata

_VARIANTS = str.maketrans({"ی": "ي", "ى": "ي", "ک": "ك"})
_MARKS_RE = re.compile("[ـً-ٟ]")


def fold(value: object) -> str:
    """NFKC + whitespace collapsed + stripped + lower; idempotent."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split()).strip().lower()


def skeleton(value: object) -> str:
    """:func:`fold` with Arabic/Persian letter variants unified and tatweel/harakat dropped."""
    return _MARKS_RE.sub("", fold(value).translate(_VARIANTS))

"""Tokenising and count helpers shared by ``perception`` and ``screen_audit``.

A leaf: it imports nothing from the mobile package, so ``screen_audit`` can use
the one tokeniser without importing ``perception`` (which imports it back).
"""

from __future__ import annotations

import re
from typing import Any

_CAMEL_LOWER_UPPER = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CAMEL_UPPER_WORD = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_WORD_RE = re.compile(r"[a-z0-9]+")


def split_camel(text: object) -> str:
    """``DeleteAccountButton`` -> ``Delete Account Button``.

    Lives here, and not in ``executor``, because ``perception`` is the module
    ``executor`` already imports -- the other direction would be a cycle. Having
    ONE tokenising idiom in this package is the point: the destructive guard,
    the role lexicon and the credential mask all ask the same question of a
    string, and three private copies of the answer is how they drift.
    """
    if not text:
        return ""
    return _CAMEL_UPPER_WORD.sub(" ", _CAMEL_LOWER_UPPER.sub(" ", str(text)))


def words(*values: object) -> list:
    """Every lowercase word in *values*, camel-case runs split first."""
    out: list = []
    for value in values:
        out.extend(_WORD_RE.findall(split_camel(value).lower()))
    return out


def _count(value: Any) -> int:
    """A drop count from a packet, or 0 -- never an exception.

    A stored packet is an outside input by the time it is read, and a bare
    ``int()`` on a field an older build never wrote (or a hostile one wrote as a
    string) would discard the ENTIRE screen block through the caller's
    `except`. Losing the screen to a malformed count is the shape this whole
    file's history is made of.
    """
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number > 0 else 0

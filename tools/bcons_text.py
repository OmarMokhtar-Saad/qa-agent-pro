"""Token helpers for ``bound_consistency``: numbers and subject words."""

from __future__ import annotations

import re

# Words that carry no field identity. Kept small on purpose: the distinctive-
# token step below removes anything two ranges share, so a generic word that
# appears in both subjects drops out without being listed here.
_SUBJECT_STOPWORDS = frozenset(
    """
    the a an of in on for and or with to be is are can cannot from at by that
    this each per set sets setting shows show app user must should within so
    far value values field between above below their its
    """.split()
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]*")


def _number(raw: str) -> float:
    return float(raw.replace(",", ""))


def _ordered_tokens(text: str) -> list[str]:
    """Content words of ``text`` in order, with hyphenated forms also split.

    A split fragment follows its parent immediately, which is what lets
    ``label`` recognise one and drop it.
    """
    out: list[str] = []
    for word in _WORD_RE.findall((text or "").lower()):
        if len(word) < 3 or word in _SUBJECT_STOPWORDS:
            continue
        if word not in out:
            out.append(word)
        if "-" not in word:
            continue
        for part in word.split("-"):
            if len(part) < 3 or part in _SUBJECT_STOPWORDS or part in out:
                continue
            out.append(part)
    return out


def _tokens(text: str) -> set[str]:
    """Content words of ``text``, with hyphenated forms also split."""
    return set(_ordered_tokens(text))

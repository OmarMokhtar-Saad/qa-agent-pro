"""Matching and the pure decision of resolve-or-ask (leaf of resolve)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, NamedTuple, Optional, Sequence

from tools.mobile.resolve_exact import tolerant_exact
from tools.mobile.resolve_types import (
    _IGNORED_WORDS,
    ASK,
    NONE,
    RESOLVED,
    AskCb,
    Candidate,
    Resolution,
)


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def _words(text: object) -> list:
    found = re.findall(r"[a-z0-9]+", _norm(text))
    return [w for w in found if w not in _IGNORED_WORDS]


def fuzzy_match(text: str, cand: Candidate) -> bool:
    """Every meaningful word of ``text`` is a whole token of the key or label.

    Tokens split on anything not a letter or digit (``.``, ``_``, ``-``, space),
    so "qa" matches ``com.x.qa`` but never ``com.aqar.app``.
    """
    words = _words(text)
    tokens = set(re.findall(r"[a-z0-9]+", _norm(cand.key + " " + cand.label)))
    return bool(words) and all(w in tokens for w in words)


def exact_key_match(text: str, cand: Candidate) -> bool:
    """The whole key, case-insensitively: for serials, where a prefix is not a match."""
    return _norm(text) == _norm(cand.key)


@dataclass(frozen=True)
class PickOpts:
    """How to pick: the noun in questions, an explicit value, the matcher and
    the ask callback (``decide`` ignores ``ask``)."""

    noun: str = "app"
    explicit: Optional[str] = None
    match: Callable[[str, Candidate], bool] = fuzzy_match
    ask: Optional[AskCb] = None


_DEFAULT = PickOpts()


class _Ask(NamedTuple):
    status: str
    wanted: str
    noun: str


def _asking(spec: _Ask, options: tuple) -> Resolution:
    if spec.status == NONE:
        head = "No " + spec.noun + " matches " + repr(spec.wanted) + "."
        head += (
            " These are available:" if options else " Ask the user for the exact name."
        )
    elif spec.wanted:
        head = (
            "More than one "
            + spec.noun
            + " matches "
            + repr(spec.wanted)
            + ". Which one?"
        )
    else:
        head = "Which " + spec.noun + "?"
    return Resolution(spec.status, None, tuple(options), head)


def _exact(pool: tuple, wanted: str) -> list:
    return [c for c in pool if _norm(c.key) == wanted] if wanted else []


def _ends_with_last_word(text: str, cand: Candidate) -> bool:
    """The key or label ENDS with the hint's last word: "shop qa" favours
    ``com.x.shop.qa`` over ``com.x.shop.qa.staging``."""
    last = (_words(text) or [""])[-1]
    return any(
        (re.findall(r"[a-z0-9]+", _norm(part)) or [""])[-1] == last
        for part in (cand.key, cand.label)
    )


def _matches(text: str, pool: tuple, opts: PickOpts) -> tuple:
    """The candidates matching the hint. For a hint of several words, several
    hits are narrowed to those whose name ends the way the hint does (all of
    them if none do); a one-word hint keeps every flavour so the tester picks."""
    if not _norm(text):
        return pool
    hits = tuple(c for c in pool if opts.match(text, c))
    if len(_words(text)) < 2:
        return hits
    best = tuple(c for c in hits if _ends_with_last_word(text, c))
    return best if len(hits) > 1 and best else hits


def _tolerant(text: str, pool: tuple, opts: PickOpts) -> Optional[Resolution]:
    """The one candidate equal to the hint ignoring case, spaces and punctuation
    (fuzzy matching only; ids such as serials stay exact)."""
    if opts.match is not fuzzy_match:
        return None
    found = tolerant_exact(text, pool)
    if len(found) != 1:
        return None
    return Resolution(RESOLVED, found[0].key, (found[0],), how="exact")


def decide(
    text: str, candidates: Sequence[Candidate], opts: PickOpts = _DEFAULT
) -> Resolution:
    """The pure decision. No I/O, no callback; see the module docstring."""
    pool = tuple(candidates)
    if opts.explicit:
        return Resolution(RESOLVED, str(opts.explicit), pool, how="explicit")
    wanted = _norm(text)
    exact = _exact(pool, wanted)
    if len(exact) == 1:
        return Resolution(RESOLVED, exact[0].key, (exact[0],), how="exact")
    tolerant = _tolerant(text, pool, opts)
    if tolerant is not None:
        return tolerant
    hits = _matches(text, pool, opts)
    if len(hits) == 1:
        return Resolution(RESOLVED, hits[0].key, hits, how="single")
    if hits:
        return _asking(_Ask(ASK, wanted, opts.noun), hits)
    return _asking(_Ask(NONE, wanted, opts.noun), pool)


def _pick(options: Sequence[Candidate], answer: Optional[str]) -> Optional[Candidate]:
    wanted = _norm(answer)
    if not wanted:
        return None
    for cand in options:
        if wanted in (_norm(cand.display()), _norm(cand.key)):
            return cand
    return None

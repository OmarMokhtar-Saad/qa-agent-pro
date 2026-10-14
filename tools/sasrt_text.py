"""Token and phrase helpers for step_assertion (see tools/step_assertion.py)."""

from __future__ import annotations

import logging
import re
from typing import Any

from config.settings_guard import never_raise

logger = logging.getLogger("tools.step_assertion")

# Phrases that assert nothing. SPLIT into two tiers, because they are not
# equally safe to match as substrings.
#
# _NEVER_AN_ASSERTION is matched ANYWHERE in the expected result: there is no
# sentence in which "the step completes successfully" contributes a claim that
# could fail, and on the observed corpus it is always followed by a colon and
# the action verbatim.
#
# _WEAK_PHRASES must DOMINATE the expected result instead. Unanchored, they
# are a false-positive risk: "no error occurs and the balance updates to SAR
# 75.50" is a perfectly good assertion that happens to contain one of them.
# None of the three occurred at all on the TICKET-5692 corpus, so the dominance
# rule below is reasoned rather than measured -- which is exactly why it is
# the CONSERVATIVE form. See the module docstring's stated limitation.
_NEVER_AN_ASSERTION = ("the step completes successfully",)
_WEAK_PHRASES = (
    "works as expected",
    "no error occurs",
    "as described above",
)

# How much of the expected result a _WEAK_PHRASE must account for before the
# expected result counts as nothing BUT that phrase.
_WEAK_PHRASE_DOMINANCE = 0.60

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = frozenset(
    "the a an of in on for and or to be is are with that this it its".split()
)


def tokens(text: str) -> set[str]:
    """Content words of *text*, lowercased, stopwords and 1-2 letter noise out."""
    return {
        word.lower().strip("'")
        for word in _WORD_RE.findall(text or "")
        if len(word) > 2 and word.lower() not in _STOPWORDS
    }


def action_overlap(action: str, expected: str) -> float:
    """Fraction of the ACTION's content tokens the expected result repeats.

    Measured over the action, not the union: an expected result that repeats the
    whole action AND adds a real assertion is fine, and normalising by the union
    would let a verbose tautology score low simply by being long.
    """
    with never_raise(
        logger,
        "action_overlap failed - treating the step as clean",
        level=logging.ERROR,
        exc_info=True,
    ):
        action_tokens = tokens(action)
        if not action_tokens:
            return 0.0
        return len(action_tokens & tokens(expected)) / len(action_tokens)
    return 0.0


def _weak_dominates(lowered: str) -> bool:
    """True when one weak phrase accounts for most of the (lowered) text."""
    return any(
        phrase in lowered and len(phrase) / len(lowered) >= _WEAK_PHRASE_DOMINANCE
        for phrase in _WEAK_PHRASES
    )


def has_generic_phrase(expected: str) -> bool:
    """True when the expected result asserts nothing that could fail.

    Two tiers, deliberately: a never-an-assertion phrase counts wherever it
    appears, while a merely weak phrase must account for most of the text --
    otherwise "no error occurs and the balance updates to SAR 75.50", which is
    a real assertion, would be flagged.
    """
    try:
        lowered = " ".join((expected or "").lower().split())
        if not lowered:
            return False
        return any(p in lowered for p in _NEVER_AN_ASSERTION) or _weak_dominates(
            lowered
        )
    except (AttributeError, TypeError):
        return False


def _get(obj: object, name: str, default: object = None) -> Any:
    """Read ``name`` off a model OR a plain mapping. Never raises.

    Every reader below used ``getattr`` alone. Call sites in this tree pass
    Pydantic ``TestCase`` models, so that worked -- but every artifact ON DISK is
    JSON, and a caller handing those straight in got an empty result with no
    error and no log line, which "Returns [] on any internal error. Never raises."
    made indistinguishable from a clean suite. Accepting both shapes is cheaper
    than a warning nobody reads, and it makes the corpus files a usable fixture.
    """
    with never_raise():
        if isinstance(obj, dict):
            got = obj.get(name, default)
        else:
            got = getattr(obj, name, default)
        return default if got is None else got
    return default


def _tokens(text: str) -> set[str]:
    """Legacy name of :func:`tokens` (WX removes it)."""
    return tokens(text)

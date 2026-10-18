"""Whether a goal says the run starts from the device's home screen, or asks
to check that the home screen is showing."""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"[.;!?](?=\s|$)|\n")
_CLAUSE = re.compile(r",|\bthen\b|\band\b", re.IGNORECASE)
_HOME = re.compile(r"\bhome\s*screen\b|\blauncher\b", re.IGNORECASE)
_START = re.compile(r"\bstart(?:s|ing)?\b|\bbegin|\binitial|\balready\b", re.IGNORECASE)
_VERIFY = re.compile(
    r"confirm|verify|check|ensure|make sure|visible|\bsee\b|shown|displayed|"
    r"appears?|observe|pre-?flight",
    re.IGNORECASE,
)
_INSTALL = re.compile(r"app\s*tester|install|update|download", re.IGNORECASE)


def _home_sentences(goal: str) -> list[str]:
    return [p for p in _SENTENCE_END.split(str(goal or "")) if _HOME.search(p)]


def _home_clauses(sentence: str) -> list[str]:
    """The parts of *sentence* (split at commas, "then", "and") naming the home
    screen, so a verb about something else ("check Wi-Fi") is not held against it."""
    return [c for c in _CLAUSE.split(sentence) if _HOME.search(c)]


def _clause_starts(clause: str) -> bool:
    return bool(_START.search(clause)) and not _VERIFY.search(clause)


def goal_starts_at_home(goal: str) -> bool:
    """True when a clause says the run starts or already is on the home screen
    or launcher, asks for no check of it and no sentence there asks to install.
    A goal that asks to confirm or verify the home screen is NOT such a start:
    the app is still launched; a verify word about something else in the same
    sentence ("Begin on the launcher, then check Wi-Fi") does not count."""
    for part in _home_sentences(goal):
        if any(_clause_starts(c) for c in _home_clauses(part)):
            return not _INSTALL.search(part)
    return False


_PRONOUN_CHECK = re.compile(
    r"^\W*(?:verify|confirm|check|ensure|make sure)\s+(?:that\s+)?(?:it|this|that)\b",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"app\s*tester|install|update|download|launch|\bopen", re.IGNORECASE
)


def _check_clauses(sentence: str) -> list[str]:
    """Clauses that check the home screen: one naming it with a verify word, or
    "verify it ..." right after a clause naming it (the pronoun is the home screen)."""
    parts = _CLAUSE.split(sentence)
    return [
        c
        for i, c in enumerate(parts)
        if (_HOME.search(c) and _VERIFY.search(c))
        or (i and _HOME.search(parts[i - 1]) and _PRONOUN_CHECK.search(c))
    ]


def _first_check(goal: str) -> str:
    """The first clause of *goal* that checks the home screen, or ``""``."""
    for part in _home_sentences(goal):
        found = _check_clauses(part)
        if found:
            return found[0]
    return ""


def goal_checks_home(goal: str) -> bool:
    """True when the goal asks to confirm, verify or see the home screen."""
    return bool(_first_check(goal))


def home_check_is_preflight(goal: str) -> bool:
    """True when the home check comes before any install, App Tester, launch or
    open clause of the goal, so the server can do it itself before those steps."""
    clause = _first_check(goal)
    at = str(goal).find(clause) if clause else -1
    return at >= 0 and not _ACTION.search(str(goal)[:at])

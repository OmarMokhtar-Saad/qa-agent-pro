"""The app name a goal quotes next to App Tester or install wording."""

from __future__ import annotations

import re

_QUOTED = re.compile(
    r"(?<!\w)(?:'([^'\n]{2,40})'|\"([^\"\n]{2,40})\"|‘([^’\n]{2,40})’"
    r"|“([^”\n]{2,40})”)(?!\w)"
)
_SENTENCE_END = re.compile(r"[.;!?](?=\s|$)|\n")
_INSTALL_CUE = re.compile(
    r"app\s*tester|install|update|download|\bbuild\b|(?:app|display)\s+name"
    r"|app\s+list",
    re.IGNORECASE,
)
_MAX_WORDS = 5
_NAMED_CUE = re.compile(r"\b(?:named\s+exactly|named|called)\s+", re.IGNORECASE)
_APP_CUE = re.compile(r"\bapp\s+", re.IGNORECASE)
_CLAUSE_END = re.compile(
    r"\s+(?:in|from|and|then|on|to|with|at|of|via)\s|[,:()\[\]]|\s[-–—]\s",
    re.IGNORECASE,
)
_NOT_A_NAME = frozenset(
    "tester list store name icon drawer screen bar page button menu distribution".split()
)


def _sentence(goal: str, match: re.Match) -> str:
    """The sentence of ``goal`` that holds ``match``."""
    before = list(_SENTENCE_END.finditer(goal, 0, match.start()))
    start = before[-1].end() if before else 0
    after = _SENTENCE_END.search(goal, match.end())
    return goal[start : after.start() if after else len(goal)]


def _unquoted_name(sentence: str, cue: re.Match) -> str | None:
    """The capitalised name after a "named"/"called"/"app" cue, cut at the clause end."""
    rest = sentence[cue.end() :]
    end = _CLAUSE_END.search(rest)
    words = (rest[: end.start()] if end else rest).split()
    if not words or len(words) > _MAX_WORDS:
        return None
    if not (words[0][0].isupper() or words[0][0].isdigit()):
        return None
    return None if words[0].lower() in _NOT_A_NAME else " ".join(words)


def _names_after(text: str, cue: re.Pattern) -> set:
    """The unquoted names after ``cue`` in the install sentences of ``text``."""
    names: set = set()
    for part in _SENTENCE_END.split(text):
        if _INSTALL_CUE.search(part):
            names |= {_unquoted_name(part, c) for c in cue.finditer(part)}
    names.discard(None)
    return names


def _unquoted_label(text: str) -> str | None:
    """The one unquoted name next to an install cue; ``None`` for zero or several.
    "named X"/"called X" wins over the weaker "app X" ("App Tester (Firebase App
    Distribution)" must not hide "the app named X")."""
    for cue in (_NAMED_CUE, _APP_CUE):
        names = _names_after(text, cue)
        if names:
            return names.pop() if len(names) == 1 else None
    return None


def _install_quoted(text: str) -> set:
    """Every quoted string sharing a sentence with App Tester or install wording."""
    return {
        next(g for g in m.groups() if g).strip()
        for m in _QUOTED.finditer(text)
        if _INSTALL_CUE.search(_sentence(text, m))
    }


def goal_install_names(goal: str) -> tuple:
    """The short quoted names in the install sentences of *goal* (a quoted
    button in another sentence is not an app name)."""
    found = _install_quoted(str(goal or ""))
    return tuple(sorted(n for n in found if n and len(n.split()) <= _MAX_WORDS))


def goal_app_label(goal: str) -> str | None:
    """The single quoted name sharing a sentence with App Tester or install
    wording; ``None`` for zero or several, so a quoted button never counts.
    With no quoted name there, one unquoted name after "named X", "called X"
    or "app X" in such a sentence."""
    text = str(goal or "")
    found = _install_quoted(text)
    names = {n for n in found if n and len(n.split()) <= _MAX_WORDS}
    if names:
        return names.pop() if len(names) == 1 else None
    return None if found else _unquoted_label(text)


def goal_quoted_names(goal: str) -> tuple:
    """Every short quoted name in *goal*, in order (the app may be one of them)."""
    seen: list = []
    for m in _QUOTED.finditer(str(goal or "")):
        name = next(g for g in m.groups() if g).strip()
        if name and len(name.split()) <= _MAX_WORDS and name not in seen:
            seen.append(name)
    return tuple(seen)

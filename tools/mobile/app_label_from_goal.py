"""The app name a goal quotes next to App Tester or install wording."""

from __future__ import annotations

import re

_QUOTED = re.compile(
    r"(?<!\w)(?:'([^'\n]{2,40})'|\"([^\"\n]{2,40})\"|‘([^’\n]{2,40})’"
    r"|“([^”\n]{2,40})”)(?!\w)"
)
_SENTENCE_END = re.compile(r"[.;!?](?=\s|$)|\n")
_INSTALL_CUE = re.compile(
    r"app\s*tester|install|update|download|\bbuild\b|(?:app|display)\s+name",
    re.IGNORECASE,
)
_MAX_WORDS = 5


def _sentence(goal: str, match: re.Match) -> str:
    """The sentence of ``goal`` that holds ``match``."""
    before = list(_SENTENCE_END.finditer(goal, 0, match.start()))
    start = before[-1].end() if before else 0
    after = _SENTENCE_END.search(goal, match.end())
    return goal[start : after.start() if after else len(goal)]


def goal_app_label(goal: str) -> str | None:
    """The single quoted name sharing a sentence with App Tester or install
    wording; ``None`` for zero or several, so a quoted button never counts."""
    text = str(goal or "")
    found = {
        next(g for g in m.groups() if g).strip()
        for m in _QUOTED.finditer(text)
        if _INSTALL_CUE.search(_sentence(text, m))
    }
    names = {n for n in found if n and len(n.split()) <= _MAX_WORDS}
    return names.pop() if len(names) == 1 else None

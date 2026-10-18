"""Whether a goal says the run starts from the device's home screen."""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"[.;!?](?=\s|$)|\n")
_HOME = re.compile(r"\bhome\s*screen\b|\blauncher\b", re.IGNORECASE)
_START = re.compile(
    r"pre-?flight|\bstart(?:s|ing)?\b|\bbegin|\bfirst\b|\binitial", re.IGNORECASE
)
_INSTALL = re.compile(r"app\s*tester|install|update|download", re.IGNORECASE)


def goal_starts_at_home(goal: str) -> bool:
    """True when one sentence names the home screen or launcher as the start
    (preflight, start, begin) and no sentence there asks to install."""
    for part in _SENTENCE_END.split(str(goal or "")):
        if _HOME.search(part) and _START.search(part):
            return not _INSTALL.search(part)
    return False

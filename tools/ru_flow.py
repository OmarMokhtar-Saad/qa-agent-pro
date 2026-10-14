"""Requirement-unit flow splitting: actor-led steps, numbered lists and business rules."""

from __future__ import annotations

import logging

from tools import ru_patterns as rp
from tools.ru_consts import _ACTORS, _NON_ACTOR_PRECEDERS

logger = logging.getLogger("tools.requirement_units")


def _starts_step(words: list[str], index: int) -> bool:
    """True when ``words[index]`` is an actor word that opens a new step."""
    if words[index].strip(".,;:!?\"'()").lower() not in _ACTORS:
        return False
    previous = words[index - 1].strip("\"'()").lower()
    if previous.endswith((".", ";", ":")):
        return True
    return previous.strip(".,;:!?") not in _NON_ACTOR_PRECEDERS


def _group_steps(words: list[str]) -> list[str]:
    """Group ``words`` into steps, cutting wherever :func:`_starts_step` says so."""
    steps: list[str] = []
    current: list[str] = []
    for index, word in enumerate(words):
        if current and _starts_step(words, index):
            steps.append(" ".join(current).strip(" .;"))
            current = [word]
        else:
            current.append(word)
    if current:
        steps.append(" ".join(current).strip(" .;"))
    return [s for s in steps if s]


# Split a run-on flow cell where a fresh ACTOR begins a new step.
#
# A bare lookahead on "user"/"system" is not enough: those words also appear
# mid-clause ("the user", "directs user to"), which shredded the Alternative
# Flow into fragments. A new step therefore starts only when the actor word is
# at the very beginning or follows a word that cannot precede a subject
# (``_NON_ACTOR_PRECEDERS``), or follows sentence punctuation.
def _split_on_actors(text: str) -> list[str]:
    """Split a run-on flow cell where a fresh ACTOR begins a new step."""
    try:
        words = rp._WORD_RE.findall(text or "")
        if not words:
            return []
        return _group_steps(words)
    except Exception:
        logger.exception("_split_on_actors failed - treating the cell as one step")
        return [text] if text else []


# Split a flow cell into individual steps.
#
# Prefers an explicit numbered/bulleted list; falls back to splitting on
# actor-led sentence starts, which is how the use-case tables in this project
# are actually written (one run-on cell, no separators).
def _split_flow(cell: str) -> list[str]:
    """Split a flow cell into individual steps."""
    try:
        text = (cell or "").strip()
        if not text:
            return []
        parts = [p.strip() for p in rp._ENUM_SPLIT_RE.split(text) if p.strip()]
        if len(parts) > 1:
            return parts
        parts = _split_on_actors(text)
        if len(parts) > 1:
            return parts
        return [text]
    except Exception:
        logger.exception("_split_flow failed - treating the cell as one step")
        return [cell] if cell else []


def _rules_from_matches(text: str, matches: list) -> list[tuple[str | None, str]]:
    """(declared_id, body) pairs for each rule-id match, skipping empty bodies."""
    out: list[tuple[str | None, str]] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip(" .;:-")
        declared = rp.WS_RE.sub("", match.group("id")).upper()
        if body:
            out.append((declared, body))
    return out


# Split a Business Rules cell into (declared_id, text) pairs.
#
# The declared id is kept verbatim when the ticket states one (``BR02``) so
# duplicate-id detection can report the ticket's own numbering defect; the
# caller still assigns a unique sequential unit id.
def _split_rules(cell: str) -> list[tuple[str | None, str]]:
    """Split a Business Rules cell into (declared_id, text) pairs."""
    try:
        text = (cell or "").strip()
        if not text:
            return []
        matches = list(rp._RULE_ID_RE.finditer(text))
        if not matches:
            return [(None, part) for part in _split_flow(text)]
        return _rules_from_matches(text, matches)
    except Exception:
        logger.exception("_split_rules failed - returning no rules")
        return []

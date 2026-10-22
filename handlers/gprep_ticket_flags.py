"""Ticket-level flags for the first prepare reply (K-15).

Uses only text the server already holds (the grounded ticket context): a
component tag that conflicts with UI-described content, and ticket keys the
ticket itself marks as duplicates. Only THIS ticket's own text and its own link
lines are read; the parent block and sibling stories belong to other tickets.
Heuristic, so every flag says "possible"."""

from __future__ import annotations

import re

_BACKEND_TAG = re.compile(r"\[(?:BE|BACKEND|API|SERVER)\]", re.I)
_FRONTEND_TAG = re.compile(r"\[(?:FE|FRONTEND|UI|MOBILE|APP)\]", re.I)
_UI_WORDS = re.compile(
    r"\b(?:screen|button|page|tab|dialog|toggle|icon|layout|banner|tooltip)s?\b", re.I
)
_KEY = r"[A-Z][A-Z0-9]+-\d+"
# Lazy filler and a word boundary before the key: the key is captured whole.
_DUP = re.compile(rf"\b(?:duplicates?)\b\D{{0,40}}?\b({_KEY})", re.I)
_LINK_DUP = re.compile(rf"^- duplicates ({_KEY})\b", re.I | re.M)
_LINK_DUP_BY = re.compile(rf"^- is duplicated by ({_KEY})\b", re.I | re.M)
_PARENT_MARK = "## Parent Story"
_LINKS_MARK = "Linked issues (context only):"
_SIBLINGS_MARK = "Sibling user stories under the same parent"
_UI_NEEDED = 3


def _tag_conflict(text: str) -> str:
    """A note when a backend-style tag sits on a ticket that describes UI."""
    head = text[:600]
    if _BACKEND_TAG.search(head) and not _FRONTEND_TAG.search(head):
        if len(_UI_WORDS.findall(_own_text(text))) >= _UI_NEEDED:
            return (
                "The ticket is tagged as backend work but its text describes "
                "screens/UI; confirm the component tag is right."
            )
    return ""


def _own_text(text: str) -> str:
    """The ticket's own text: everything before the parent block."""
    return text.split(_PARENT_MARK, 1)[0]


def _link_lines(text: str) -> str:
    """The ticket's own "Linked issues" lines (not the parent, not siblings)."""
    if _LINKS_MARK not in text:
        return ""
    return text.split(_LINKS_MARK, 1)[1].split(_SIBLINGS_MARK, 1)[0]


def _upper(keys: list[str]) -> list[str]:
    return sorted({k.upper() for k in keys})


def _duplicates(text: str) -> str:
    """Notes naming keys this ticket marks as duplicated, or duplicated by."""
    found = _DUP.findall(_own_text(text)) + _LINK_DUP.findall(_link_lines(text))
    by = _upper(_LINK_DUP_BY.findall(_link_lines(text)))
    notes = []
    if found:
        notes.append("Possible duplicate of " + ", ".join(_upper(found)) + ".")
    if by:
        notes.append(", ".join(by) + " is linked as a duplicate of this ticket.")
    return "\n".join(notes)


def flag_lines(payload: dict | None) -> list[str]:
    """K-15: blank line + flag block for a payload's ticket text, or no lines."""
    flags = ticket_flags(str((payload or {}).get("user_context") or ""))
    return ["", flags] if flags else []


def ticket_flags(text: str) -> str:
    """The '> ' flag block for the first reply, or '' when nothing is flagged."""
    notes = [*_tag_conflict(text).splitlines(), *_duplicates(text).splitlines()]
    return "\n".join(f"> \u26a0\ufe0f Ticket check: {n}" for n in notes if n)

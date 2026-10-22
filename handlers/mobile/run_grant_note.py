"""The start-reply note that says which runtime permissions were pre-granted."""

from __future__ import annotations

import re

from tools.mobile.perm_groups import GROUPS

_SHOWN = 6
# One slot, bounded by construction: a later start overwrites it. Worst case
# (two runs on two devices in one server, interleaved awaits): run B's grant
# lands between run A's grant and A's start reply, so A's note is lost. That is
# harmless -- the note is advice only, and the wrong device never gets it.
# Prompt-shaped phrases only: bare "allow", "contacts" or "notification" are
# ordinary words in a brief about a notifications tab or a location filter.
_KINDS = r"(?:location|camera|microphone|contacts?|notifications?|storage|photos?)"
_PROMPT_WORDS = re.compile(
    r"runtime permission|permission (?:prompt|dialog|sheet|request|popup)"
    r"|(?:allow|deny|grant)\w* (?:\w+ )?(?:access|permission)"
    rf"|{_KINDS} (?:access|permission|prompt|sheet|dialog|popup)"
    r"|while using the app",
    re.I,
)
_ASKED: set[str] = set()
_LAST: tuple[str, tuple[str, ...]] | None = None


def remember(serial: str, granted: list[str], denied: frozenset) -> None:
    """Keep what was pre-granted on *serial* (minus what the run then denied)."""
    global _LAST
    _ASKED.discard(serial)
    kept = tuple(p for p in granted if p not in denied)
    _LAST = (serial, kept) if kept else None


def mention(serial: str, brief: object) -> None:
    """Remember that the brief on *serial* talks about permission prompts."""
    _ASKED.discard(serial)
    if _PROMPT_WORDS.search(str(brief or "")):
        _ASKED.add(serial)


_WARN = (
    "> Warning: the brief mentions permission prompts or sheets, but granting "
    "is on, so those prompts will NOT appear. To see them, re-call with "
    "`grant_permissions=false`.\n\n"
)


def _names(perms: tuple[str, ...]) -> list[str]:
    owned = {p: name for name, group in GROUPS.items() for p in group}
    names = {owned.get(p) or p.rsplit(".", 1)[-1].lower() for p in perms}
    return sorted(names)


def _note(perms: tuple[str, ...]) -> str:
    names = _names(perms)
    listed = ", ".join(names[:_SHOWN])
    more = f", +{len(names) - _SHOWN} more" if len(names) > _SHOWN else ""
    return (
        f"> Pre-granted {len(perms)} runtime permissions ({listed}{more}) before "
        "step one, so their system permission dialogs will not appear. To test a "
        "prompt, re-call with `grant_permissions=false` (or `deny_permissions`).\n\n"
    )


def take(serial: str) -> str:
    """The note for *serial*'s start reply (clearing it), or ``""``."""
    global _LAST
    last = _LAST
    if last is None or last[0] != serial:
        return ""
    _LAST = None
    warn = _WARN if serial in _ASKED else ""
    _ASKED.discard(serial)
    return _note(last[1]) + warn

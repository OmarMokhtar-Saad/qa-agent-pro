"""The value scrubber behind every run file: known secret values are removed wherever they appear.

Key-based redaction cannot see a secret under an unrecognised key (an assert's
``text``, a UI-dump node's ``desc``, a manifest field). This registry holds every
secret value the process has been given -- typed into a credential field, marked
``secret``, or supplied as a tester input -- and :func:`scrub` replaces it in any
string. :func:`tools.mobile.run_redact.redact` and
:func:`tools.mobile.act_redact.redact_action` both call it, so every run-store write
passes it. Console and logcat evidence is written by ``run_io._write_text`` after
its own pass through ``tools.mobile_shared.scrub``, a separate registry.
Values live in process memory only, never on disk.

Matching rules (so an unrelated string is not hit):

* a value matches only as a whole token (not inside a word), case-sensitively;
* an all-digit value shorter than 6 digits (a 4-digit OTP) is masked only when it
  is the WHOLE text of the field, never inside bounds, counts or prose;
* :data:`STRUCTURAL_KEYS` (bounds, ids, class, package) are never scrubbed;
* a value registered for a run (``owner``) is dropped by :func:`drop` when the
  run's held inputs are forgotten.

The registry is read from worker threads (the finish scan runs in
``asyncio.to_thread``) while the loop thread registers the next run's values, so
every access holds :data:`LOCK`.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable

from tools.mobile.act_cred import SECRET_MASK, is_credential_action

STRUCTURAL_KEYS = frozenset(
    {
        "bounds",
        "rid",
        "id",
        "resource_id",
        "resource-id",
        "class",
        "cls",
        "package",
        "pkg",
    }
)
_SHORT_DIGITS = 6

# value -> (pattern, owner, whole-field-only)
_known: dict[str, tuple[re.Pattern, str, bool]] = {}
LOCK = threading.Lock()


def register(value: object, owner: str = "") -> None:
    """Remember *value* (a string of 4+ characters) as a secret to scrub."""
    text = value.strip() if isinstance(value, str) else ""
    if len(text) < 4 or text == SECRET_MASK:
        return
    pattern = re.compile(r"(?<!\w)" + re.escape(text) + r"(?!\w)")
    short = text.isdigit() and len(text) < _SHORT_DIGITS
    with LOCK:
        if text in _known:
            return
        if len(_known) >= 256:
            _known.pop(next(iter(_known)))
        _known[text] = (pattern, owner, short)


def register_all(values: Iterable[object], owner: str = "") -> None:
    """Register every value of an iterable (a tester-inputs dict's values)."""
    for value in values:
        register(value, owner)


def owned(owner: str) -> tuple:
    """The values registered for *owner* (a run), longest first. Empty owner: none."""
    if not owner:
        return ()
    with LOCK:
        values = [text for text, entry in _known.items() if entry[1] == owner]
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def drop(owner: str) -> None:
    """Forget the values registered for *owner* (a run that has ended)."""
    with LOCK:
        for text in [t for t, entry in _known.items() if owner and entry[1] == owner]:
            del _known[text]


def register_action(payload: dict) -> None:
    """Register the typed text of an action that is secret-marked or a credential."""
    if payload.get("secret") or is_credential_action(payload):
        register(payload.get("text"))
        register(payload.get("value"))


def scrub(text: str) -> str:
    """*text* with every registered secret value replaced by the mask."""
    with LOCK:
        entries = sorted(_known.items(), key=lambda item: len(item[0]), reverse=True)
    for secret, (pattern, _owner, whole_only) in entries:
        if whole_only:
            text = SECRET_MASK if text.strip() == secret else text
        else:
            text = pattern.sub(SECRET_MASK, text)
    return text


def clear() -> None:
    """Forget every registered value (tests)."""
    with LOCK:
        _known.clear()

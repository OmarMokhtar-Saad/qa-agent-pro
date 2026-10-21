"""Tell the host to pass credentials when a goal needs a login but none came."""

from __future__ import annotations

import json
import re

_LOGIN_RE = re.compile(
    r"\blog[\s-]?in\b|\bsign[\s-]?in\b|\bpassword\b|\bpasscode\b|\botp\b"
    r"|one[\s-]time (?:code|password)|\bcredentials?\b|verification code"
    r"|\busername\b|(?:تسجيل|سجل) (?:ال)?دخول|كلمة (?:المرور|السر)|رمز التحقق",
    re.IGNORECASE,
)
_MASKED_RE = re.compile(
    r"^[\s*•x#<\[({]*(?:masked|redacted|hidden)?[\s*•x#>\])}.]*$", re.I
)

LOGIN_NOTE = (
    "Note: the goal mentions a login and no tester_inputs were passed. If it "
    "needs a sign-in, ask the user for the sign-in fields (ID, "
    "password, one-time code) and pass them as tester_inputs on "
    "qa_submit_mobile_step, typed with a secret step "
    '({"op": "type", "secret": true}). Do not put the values in the goal; '
    "they are stored nowhere.\n\n"
)


def _real(value: object) -> bool:
    """True for a value that is not null, blank or masked (nested ones count)."""
    if isinstance(value, dict):
        return any(_real(v) for v in value.values())
    if isinstance(value, list):
        return any(_real(v) for v in value)
    return value is not None and not _MASKED_RE.match(str(value))


def _usable(raw: str) -> bool:
    """True when ``raw`` is JSON with at least one real (non-blank, unmasked) value."""
    try:
        loaded = json.loads(raw) if raw and raw.strip() else {}
    except (TypeError, ValueError):
        return bool(raw and raw.strip())
    return _real(loaded)


def login_needed_note(goal: str, tester_inputs: str) -> str:
    """The note text, or "" when no login is mentioned or inputs are present."""
    if not goal or not _LOGIN_RE.search(goal) or _usable(tester_inputs or ""):
        return ""
    return LOGIN_NOTE


def with_login_note(blocks: list, goal: str, tester_inputs: str) -> list:
    """Prepend the login note to the first text block when it applies."""
    note = login_needed_note(goal, tester_inputs)
    first = blocks[0] if blocks else None
    text = getattr(first, "text", None)
    if note and isinstance(text, str):
        setattr(first, "text", note + text)
    return blocks

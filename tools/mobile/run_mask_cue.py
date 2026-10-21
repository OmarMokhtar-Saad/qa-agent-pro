"""Mask the value written after a credential cue word in free text (a goal).

A goal such as ``log in with ID 123..., password Qwerty123, OTP 4321`` carries its
credentials as prose. Held-input scrubbing only knows values the host registered,
so a goal that was never registered leaked. This rule needs no registry: the word
after ``password`` / ``otp`` / ``pin`` / ``passcode`` / ``cvv`` is the secret.
Plain prose about a field ("the password is required") is never a secret.
"""

from __future__ import annotations

import re

from tools.mobile import run_consts

_ARABIC = "كلمة المرور|الرقم السري|رمز التحقق|اسم المستخدم|رقم الهوية"
_CUE_RE = re.compile(
    r"(?i)(?<![a-z])(passw(?:or)?d|pwd|passcode|otp|pin|cvv|username|id|"
    + _ARABIC
    + r")(?![a-z])(\s*(?:is|=|:)?\s*)(['\"]?)(?!\*\*\*)(?!/)([^\s,;'\"]+)\3"
)
# Keys whose text is free prose written by the host, not a UI value.
_WEAK_CUES = frozenset({"id", "username"})
GOAL_KEYS = frozenset({"goal", "sub_goal"})
_TRAIL = ".,)!?:]}"


def _weak_secret(quote: str, value: str) -> bool:
    """An ID/username value is a secret when quoted or digit-bearing; a long digit
    run is left to the ID mask, which keeps its last four digits."""
    if value.isdigit() and len(value) >= 8:
        return False
    return bool(quote) or any(c.isdigit() for c in value)


def _looks_secret(value: str) -> bool:
    """A digit, a symbol or a capital after the first letter; a bare lowercase
    (or merely capitalised) word is prose such as ``required``."""
    return (
        any(c.isdigit() for c in value)
        or any(not c.isalnum() for c in value)
        or any(c.isupper() for c in value[1:])
    )


def _secret(hit: re.Match[str]) -> str | None:
    """The value to hide for *hit* (trailing punctuation off), or None for prose."""
    cue, gap, quote, raw = hit.groups()
    value: str = raw.rstrip(_TRAIL) or raw
    if cue.lower() in _WEAK_CUES:
        return value if _weak_secret(quote, value) else None
    if quote:
        return value
    if gap.strip():
        return value if _looks_secret(value) else None
    # No connector ("pin 2 on map"): a short bare number is a count, not a code.
    digits = any(c.isdigit() for c in value)
    return value if digits and len(value) >= 3 else None


def _sub(hit: re.Match[str]) -> str:
    if _secret(hit) is None:
        return str(hit.group(0))
    return hit.group(1) + hit.group(2) + run_consts.SECRET_MASK


def mask_cues(text: str) -> str:
    """*text* with each value after a credential cue word replaced by ``***``."""
    return _CUE_RE.sub(_sub, text)


def cue_values(text: str) -> list[str]:
    """The secret values *text* carries after a cue word (what :func:`mask_cues` hides)."""
    found = (_secret(hit) for hit in _CUE_RE.finditer(text))
    return [value for value in found if value is not None]

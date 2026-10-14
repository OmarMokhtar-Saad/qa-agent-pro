"""Secret refusal for a note: held values, digit runs, API-shape scan, stated credentials."""

from __future__ import annotations

import re
from typing import Any

from config.settings_guard import never_raise
from tools import api_redact
from tools.mobile import actions, appk_consts, appk_stated, held_inputs

_DIGITS_RE = re.compile(r"\d{6,}")
#: A credential word within ``_NEAR_WORDS`` words of a VALUE-SHAPED token, in
#: either direction ("otp 4821", "enter 4821 as the PIN", "pin=1234"). A bare
#: credential word is fine ("login is required", "the code is sent by SMS"):
#: the term list names topics, not secrets, so matching it alone over-refuses.
#: Value-shaped = 4+ digits, or 5+ chars mixing letters and digits. Single
#: digits spaced out ("pin 1 2 3 4") are joined before the check.
_NEAR_WORDS = 3
_SPLIT_RE = re.compile(r"[\s:=]+")
_ALPHA_RE = re.compile(r"[a-z]+")
_DIGITS4_RE = re.compile(r"\d{4}")
_SPACED_DIGITS_RE = re.compile(r"(?<![\d.-])\d(?:[ .-]\d(?![\d.-])){3,}")


def _leaf_strings(obj: Any) -> list:
    """Every string inside ``obj``. Numbers such as ``ms`` are not text."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _leaf_strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _leaf_strings(v)]
    return []


def _is_value_token(token: str) -> bool:
    if _DIGITS4_RE.search(token):
        return True
    return (
        len(token) >= 5
        and any(ch.isdigit() for ch in token)
        and any(ch.isalpha() for ch in token)
    )


def _credential_near_value(segment: str) -> bool:
    if appk_stated._ASSIGNED_RE.search(segment) or appk_stated._states_digits(segment):
        return True
    segment = _SPACED_DIGITS_RE.sub(lambda m: re.sub(r"\D", "", m.group()), segment)
    tokens = [t.strip(".,;()[]{}\"'") for t in _SPLIT_RE.split(segment)]
    tokens = [t for t in tokens if t]
    terms = [
        i
        for i, t in enumerate(tokens)
        if any(w in actions.CREDENTIAL_TERMS for w in _ALPHA_RE.findall(t.lower()))
    ]
    if not terms:
        return False
    values = [i for i, t in enumerate(tokens) if _is_value_token(t)]
    return any(abs(i - j) <= _NEAR_WORDS for i in terms for j in values)


def _held_values(secrets: Any, source_run: Any) -> list:
    held = [str(value) for value in (secrets or ())]
    with never_raise():
        held += [str(v) for v in held_inputs.recall(str(source_run or "")).values()]
    return held


def _held_hit(low: str, held: list) -> bool:
    for value in held:
        value = value.strip()
        if len(value) >= appk_consts.HELD_VALUE_LENGTH and value.lower() in low:
            return True
    return False


def _secret_reason(blob: str, secrets: Any, source_run: Any) -> str:
    """Why *blob* looks like it holds a secret, or ``""``."""
    low = blob.lower()
    if _held_hit(low, _held_values(secrets, source_run)):
        return "it contains a value the tester typed into this run"
    if _DIGITS_RE.search(blob):
        return "it contains a run of 6 or more digits"
    # The API lane's value-shape scan: API keys, JWTs, `Bearer` tokens, grouped
    # card numbers, e-mail addresses, +phone numbers.
    if api_redact.scan_finds_secret(blob):
        return "it contains a key, token, card number or contact detail"
    if appk_stated._states_digits(blob) or any(
        _credential_near_value(seg) for seg in blob.split("\n")
    ):
        return "it states a credential value"
    return ""


def secret_reason(blob: Any, secrets: Any = (), source_run: Any = "") -> str:
    """Public face of the note scrub: why *blob* looks like it holds a secret, or ``""``.

    Flows reuse it so a saved screen string passes the SAME test a saved note does."""
    return _secret_reason(str(blob or ""), secrets, source_run)

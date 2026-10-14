"""Caps, vocabulary and name checks shared by the saved-flow modules (see ``app_flows``)."""

from __future__ import annotations

import re
from typing import Any

from tools.mobile import actions

#: Characters of a flow name.
MAX_FLOW_NAME_CHARS = 40
#: Distinct {placeholder} parameters one flow may declare.
MAX_PARAMS_PER_FLOW = 8
#: Characters of a parameter name.
MAX_PARAM_NAME_CHARS = 32
#: Characters of a secret step's field name (the action schema itself allows 80).
MAX_FIELD_CHARS = 80

ALLOWED_OPS = (
    "tap",
    "tap_text",
    "type",
    "fill",
    "clear",
    "back",
    "press",
    "scroll",
    *actions.WAIT_UNTIL_OPS,
)

REFUSED_WHY = {
    "clear_app_data": "it wipes the app",
    "open_url": "it leaves the app",
    "ask_tester": "a flow never stops to ask mid-replay",
    "done": "only the model ends a case",
    "wait": "wait for something instead (wait_until_text, _gone, _changed or _idle)",
}

#: Trace outcomes of a step that ran as planned (executor.py mints both; a secret
#: `type` step records 'ok'; 'supplied' belongs to ask_tester, which a flow refuses).
OK_OUTCOMES = frozenset(("ok", "assert_pass"))

_NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,%d}" % (MAX_FLOW_NAME_CHARS - 1))
PLACEHOLDER_RE = re.compile(
    r"[{]([a-z][a-z0-9_]{0,%d})[}]" % (MAX_PARAM_NAME_CHARS - 1)
)
CREDENTIAL_WORDS = frozenset(
    (
        "pass",
        "password",
        "passwd",
        "passcode",
        "pwd",
        "secret",
        "token",
        "otp",
        "pin",
        "code",
        "card",
        "cvv",
        "key",
        "ssn",
        "auth",
    )
)


def err(message: str) -> dict:
    return {"error": message, "content": None}


def valid_name(name: Any) -> bool:
    return isinstance(name, str) and bool(_NAME_RE.fullmatch(name))


def bad_name() -> dict:
    return err(
        "not a valid flow name (lower-case letters, digits, _ and -, at most %d characters)"
        % MAX_FLOW_NAME_CHARS
    )

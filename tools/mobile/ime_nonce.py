"""The arm-once nonce of the QA IME broadcast channel. Pure: no adb, no device.

The server arms the keyboard with a random nonce sent over stdin (never argv, never
chat); the new APK then accepts INPUT/CLEAR/QUERY/DUMP only with that nonce. A speed bump
against stray broadcasts, not a boundary against adb. State is per serial and in memory.
"""

from __future__ import annotations

import re
import secrets

#: One re-ARM-and-retry when the keyboard says it is not armed (idle timeout, restart).
REARM_MAX_RETRIES = 1

NONCE_RE = re.compile(r"[0-9a-f]{32}")

#: Result codes of the new APK. 0 and 1 keep their QUERY meaning.
REPLY_OK = 1
REPLY_ALREADY_ARMED = 2
REPLY_REFUSED = 4

PROTOCOL_LEGACY = 1
PROTOCOL_NONCE = 2

_NONCES: dict = {}
_PROTOCOLS: dict = {}

_REFUSALS = {
    "not_armed": (
        "The QA keyboard is not armed (its idle timeout elapsed or it restarted), "
        "so nothing was sent."
    ),
    "bad_nonce": (
        "The QA keyboard refused this session's nonce (another client armed it), "
        "so nothing was typed or cleared."
    ),
    "bad_request": "The QA keyboard refused a malformed request, so nothing was sent.",
    "no_service": "The QA screen reader is not connected.",
    "no_window": "The QA screen reader has no active window to read.",
    "dump_failed": "The QA screen reader failed while reading the screen.",
    "too_large": "The QA screen reader reply was too large.",
}


def new_nonce() -> str:
    return secrets.token_hex(16)


def valid_nonce(value: object) -> bool:
    return bool(NONCE_RE.fullmatch(str(value or "")))


def get(serial: str) -> str:
    return _NONCES.get(str(serial), "")


def hold(serial: str, nonce: str) -> None:
    if valid_nonce(nonce):
        _NONCES[str(serial)] = str(nonce)


def forget(serial: str) -> None:
    _NONCES.pop(str(serial), None)
    _PROTOCOLS.pop(str(serial), None)


def reset() -> None:
    _NONCES.clear()
    _PROTOCOLS.clear()


def protocol(serial: str) -> int:
    return int(_PROTOCOLS.get(str(serial), 0))


def set_protocol(serial: str, value: int) -> None:
    _PROTOCOLS[str(serial)] = int(value)


def refusal_text(reason: object) -> str:
    text = str(reason or "")
    return _REFUSALS.get(
        text,
        "The QA keyboard refused the request ("
        + (text[:40] or "no reason")
        + "), so nothing was sent.",
    )

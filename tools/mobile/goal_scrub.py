"""Device serials never enter a saved goal: a goal is shared by every device and run."""

from __future__ import annotations

import re

#: adb serials: ``emulator-5554``, ``host:port``, or a long letters-and-digits token.
_FIXED = re.compile(r"\b(?:emulator-\d+|\d{1,3}(?:\.\d{1,3}){3}:\d+)\b", re.IGNORECASE)
_TOKEN = re.compile(r"\b[a-z0-9]{8,20}\b", re.IGNORECASE)
_HEX16 = re.compile(r"[0-9a-f]{16}", re.IGNORECASE)
_SWAPS = 3  # a hardware serial swaps between letters and digits at least this often
MASK = "device"


def _serial_shaped(token: str) -> bool:
    """True for a token that swaps between letters and digits like a hardware serial.

    ``R5CR207KY7H`` does (8 swaps); an order id such as ``ORD12345678`` does not (1).
    """
    if _HEX16.fullmatch(token) and not token.isdigit() and not token.isalpha():
        return True  # emulator and some boards: 16 hex chars, too few swaps to count
    kinds = [c.isdigit() for c in token]
    return sum(a != b for a, b in zip(kinds, kinds[1:])) >= _SWAPS


def _mask_token(hit: re.Match) -> str:
    return MASK if _serial_shaped(hit.group(0)) else hit.group(0)


def scrub(goal: str, serial: str = "") -> str:
    """*goal* with the run's *serial* and anything shaped like an adb serial removed."""
    text = str(goal or "")
    if serial:
        text = re.sub(re.escape(serial), MASK, text, flags=re.IGNORECASE)
    text = _TOKEN.sub(_mask_token, _FIXED.sub(MASK, text))
    return " ".join(re.sub(r"\b(device )+device\b", MASK, text).split())

"""Watched waits, part 1: name what ended a wait early (``classify_*``).

A LEAF: it imports nothing from ``tools.mobile``. ``classify_*`` name the thing
that ended a wait early -- ``"interrupted: <what>"`` -- from NEW facts only. A
fact that was already true when the wait began (the app was already covered by
a dialog) is the situation the caller started from, not an interruption.
"""

from __future__ import annotations

import re
from typing import Any

#: The longest quotation of on-screen text inside an interruption sentence.
MAX_QUOTE_CHARS = 80

PREFIX = "interrupted: "

#: Packages of the phone app's in-call screens. UNVERIFIED on Samsung/Xiaomi
#: builds beyond the ones listed; an unlisted dialer is simply not named.
CALL_PACKAGES = frozenset(
    {
        "com.android.incallui",
        "com.google.android.dialer",
        "com.android.dialer",
        "com.samsung.android.incallui",
    }
)

#: The notification shade and the lock screen are both drawn by system UI.
SHADE_PACKAGE = "com.android.systemui"

LOCK_PHRASES = ("swipe up to unlock", "swipe to unlock")

#: adb's own words for a device that went away: ``device offline``,
#: ``device 'emulator-5554' not found``, ``no devices/emulators found``,
#: ``device unauthorized``. Anchored on the word DEVICE, so a tool on the
#: device saying ``uiautomator: not found`` is not read as a disconnect.
_DISCONNECT_RE = re.compile(
    r"\bdevice\b[^.\n]{0,80}?\b(?:offline|not found|unauthorized)\b"
    r"|\bno devices\b|\bdisconnected\b"
)

#: Lower-case fragments that mean the app or the platform is reporting a
#: failure. Deliberately narrow: a false interruption costs the model a re-plan
#: on a screen that was fine.
ERROR_PHRASES = (
    "isn't responding",
    "not responding",
    "has stopped",
    "keeps stopping",
    "internal server error",
    "something went wrong",
    "no internet connection",
    "unable to connect",
    "network error",
    "app not installed",
    "installation failed",
    "install failed",
    "couldn't be installed",
)

#: ``HTTP 500``, ``status: 503``, ``error 404`` -- a status word FIRST, so a bare
#: "500 items" or a price is not read as a server error.
_HTTP_RE = re.compile(r"\b(?:http|status|error|code)\s*[:#=-]?\s*([45]\d\d)\b")

_CALL_HEAD = "a phone call is in front of the app ("
_CALL_TAIL = "). Answer or decline it, then continue."
_SHADE_TEXT = (
    "the notification shade or the lock screen is in front of the app. "
    "Close or unlock it, then continue."
)


def _quote(text: object) -> str:
    return repr(" ".join(str(text).split())[:MAX_QUOTE_CHARS])


# ``""`` when the package did not change or is not one of the named kinds;
# the caller decides what a plain app switch is called.
def classify_package(package: object, before_package: object) -> str:
    """Name a call or the shade/lock screen that is NEW in front of the app."""
    now = str(package or "")
    if not now or now == str(before_package or ""):
        return ""
    if now in CALL_PACKAGES:
        return PREFIX + _CALL_HEAD + now[:MAX_QUOTE_CHARS] + _CALL_TAIL
    return PREFIX + _SHADE_TEXT if now == SHADE_PACKAGE else ""


def classify_texts(new_texts: Any) -> str:
    """Name an error, a failed install or a lock screen among texts that are NEW.

    *new_texts* is what the screen shows now and did not show when the wait
    began. Sorted, so the same screen always yields the same sentence.
    """
    if not new_texts:
        return ""
    for text in sorted(str(t) for t in new_texts):
        low = " ".join(text.split()).lower().replace("\u2019", "'")
        if any(phrase in low for phrase in LOCK_PHRASES):
            return PREFIX + "the screen is locked. Unlock the phone, then continue."
        if any(phrase in low for phrase in ERROR_PHRASES) or _HTTP_RE.search(low):
            return PREFIX + "the screen now shows an error: " + _quote(text)
    return ""


def classify_error(error: object) -> str:
    """Name a device that stopped answering, from a failed dump's error text."""
    low = str(error or "").lower()
    if _DISCONNECT_RE.search(low):
        return (
            PREFIX
            + "the device stopped answering ("
            + _quote(error)
            + "). Check the cable and `adb devices`, then retry."
        )
    return ""

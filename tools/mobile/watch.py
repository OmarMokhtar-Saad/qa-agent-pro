"""Watched waits: what a wait says while it runs, and what ends it early.

A LEAF: it imports nothing from ``tools.mobile``, so the executor, the emulator
and the MCP server can all use it without an import cycle.

Two jobs, both about a wait the model cannot see into:

* PROGRESS. A long wait (an emulator boot, a screen that is loading) used to be
  silent, and the tester watched nothing for minutes. ``bind_progress`` hands
  this module the host's progress callback for the current tool call
  (``mcp_server._make_progress``), and a :class:`Ticker` sends one short line
  every :data:`PROGRESS_EVERY_S` seconds. Unbound (a unit test, a script) every
  call is a no-op.
* INTERRUPTIONS. ``classify_*`` name the thing that ended a wait early --
  ``"interrupted: <what>"`` -- from NEW facts only. A fact that was already true
  when the wait began (the app was already covered by a dialog) is the
  situation the caller started from, not an interruption.

No model runs here, and nothing sleeps: the caller owns the clock and the loop.
"""

from __future__ import annotations

import contextvars
import logging
import re
import time

logger = logging.getLogger(__name__)

#: Seconds between two progress lines from one wait. Progress is a notification
#: the host may show or drop; more often than this is chatter, not information.
PROGRESS_EVERY_S = 3.0

#: The longest progress line sent to the host.
MAX_PROGRESS_CHARS = 160

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

_REAL_MONOTONIC = time.monotonic

_progress: contextvars.ContextVar = contextvars.ContextVar(
    "qa_mobile_watch_progress", default=None
)


def bind_progress(callback: object) -> None:
    """Route this tool call's wait progress to *callback* (``async (str) -> None``).

    Bound per context, so one tool call's ticks never reach another call's host.
    """
    _progress.set(callback if callable(callback) else None)


def is_bound() -> bool:
    return _progress.get() is not None


async def say(message: str) -> None:
    """Send one progress line to the host. Never raises; a no-op when unbound."""
    callback = _progress.get()
    if callback is None:
        return
    try:
        await callback(str(message)[:MAX_PROGRESS_CHARS])
    except Exception:
        logger.debug("mobile.watch: progress callback failed", exc_info=True)


class Ticker:
    """Emit ``<what> ... <n> s`` at most once per :data:`PROGRESS_EVERY_S`.

    Call :meth:`tick` once per poll; it decides whether a line is due. The clock
    is injectable and defaults to the real monotonic clock captured at import,
    so a test that swaps ``time.monotonic`` for a scripted list does not have
    its sequence consumed by progress bookkeeping.
    """

    def __init__(self, what: str, *, every: float | None = None, clock: object = None):
        self._what = str(what)
        self._every = PROGRESS_EVERY_S if every is None else float(every)
        self._clock = clock or _REAL_MONOTONIC
        self._start = self._clock()
        self._last = self._start

    async def tick(self, detail: str = "") -> bool:
        """True when a line was sent. Costs nothing while no host is bound."""
        if not is_bound():
            return False
        now = self._clock()
        if now - self._last < self._every:
            return False
        self._last = now
        line = self._what + " ... " + str(int(now - self._start)) + " s"
        if detail:
            line += " (" + str(detail) + ")"
        await say(line)
        return True


def _quote(text: object) -> str:
    return repr(" ".join(str(text).split())[:MAX_QUOTE_CHARS])


def classify_package(package: object, before_package: object) -> str:
    """Name a call or the shade/lock screen that is NEW in front of the app.

    ``""`` when the package did not change or is not one of the named kinds;
    the caller decides what a plain app switch is called.
    """
    now = str(package or "")
    before = str(before_package or "")
    if not now or now == before:
        return ""
    if now in CALL_PACKAGES:
        return (
            PREFIX
            + "a phone call is in front of the app ("
            + now[:MAX_QUOTE_CHARS]
            + "). Answer or decline it, then continue."
        )
    if now == SHADE_PACKAGE:
        return (
            PREFIX
            + "the notification shade or the lock screen is in front of the app. "
            "Close or unlock it, then continue."
        )
    return ""


def classify_texts(new_texts: object) -> str:
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

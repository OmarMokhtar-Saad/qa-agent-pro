"""One DUMP broadcast to the qa-ime AccessibilityService, and the provider built on it."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import zlib

from config.settings_guard import never_raise
from tools.mobile import a11y_events, a11y_retry, adb, ime, ime_nonce, ime_proof

logger = logging.getLogger("tools.mobile.a11y")

#: Seconds one DUMP broadcast may take before the run falls back to uiautomator.
A11Y_DUMP_TIMEOUT_S = 8

#: Characters of base64 one DUMP reply may carry (the binder limit is near 1 MB).
A11Y_MAX_REPLY_CHARS = 1000000

#: Consecutive failed fast dumps before the provider is dropped for the run.
A11Y_MAX_CONSECUTIVE_FAILURES = 3

#: Consecutive ``no_window`` refusals before the provider is dropped for the run.
#: qa-ime 1.2.0 reads every app and dialog window, so
#: ``no_window`` means no readable window at all (a screen transition); each refusal
#: falls back to uiautomator for that dump only.
A11Y_MAX_CONSECUTIVE_NO_WINDOW = 10

_NO_WINDOW_REFUSAL = "refused: no_window"

_FAILS: dict = {}
_NO_WINDOW: dict = {}


def _inflate(b64: str) -> dict:
    raw = base64.b64decode(b64, validate=True)
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = inflater.decompress(raw, adb.MAX_DUMP_BYTES + 1)
    if len(out) > adb.MAX_DUMP_BYTES or inflater.unconsumed_tail:
        return {
            "error": "the a11y dump exceeds the "
            + str(adb.MAX_DUMP_BYTES)
            + " byte cap",
            "content": None,
        }
    return {"error": None, "content": out}


def decode(b64: str) -> dict:
    """``base64(gzip(xml))`` -> XML text. Bounded: a bomb is an error, never truncated."""
    try:
        if len(b64) > A11Y_MAX_REPLY_CHARS:
            return {"error": "the a11y reply exceeds its cap", "content": None}
        inflated = _inflate(b64)
        if inflated["error"]:
            return inflated
        text = inflated["content"].decode("utf-8", errors="replace")
        if not text.lstrip().startswith("<"):
            return {"error": "the a11y reply was not XML", "content": None}
        return {"error": None, "content": text}
    except (binascii.Error, zlib.error, ValueError) as exc:
        return {
            "error": "the a11y reply could not be decoded (" + str(exc)[:80] + ")",
            "content": None,
        }


def _dump_action(serial: str) -> tuple:
    if not ime_nonce.get(serial):
        return "", {"error": "the QA keyboard is not armed", "content": None}
    actions = ((ime.manifest() or {}).get("content") or {}).get("actions") or {}
    action = str(actions.get("dump") or "")
    if not action:
        return "", {"error": "no dump action is pinned", "content": None}
    return action, None


def _reply_error(reply: dict) -> dict | None:
    if int(reply.get("result", -1)) == ime_nonce.REPLY_REFUSED:
        return {
            "error": "refused: " + str(reply.get("refusal") or "unknown"),
            "content": None,
        }
    if int(reply.get("result", -1)) != 1 or not reply.get("dump"):
        return {"error": "the a11y service did not answer", "content": None}
    return None


def _failed(exc: BaseException | None) -> dict:
    return {
        "error": "a11y dump failed (" + (str(exc)[:80] or type(exc).__name__) + ")",
        "content": None,
    }


def _record_events(serial: str, reply: dict, decoded: dict) -> None:
    """Keep the reply's ``n:`` event clock; a dump without it clears the clock."""
    clock = a11y_events.parse(str(reply.get("events") or ""))
    a11y_events.record(serial, clock if decoded.get("error") is None else None)


async def fetch_dump(serial: str) -> dict:
    """One DUMP broadcast -> ``{"error", "content": xml}``. Never raises."""
    with never_raise(logger, "mobile.a11y.fetch_dump failed", exc_info=True) as g:
        action, error = _dump_action(serial)
        if error:
            return error
        sent = await asyncio.wait_for(
            ime.send(serial, action), timeout=A11Y_DUMP_TIMEOUT_S
        )
        if sent.get("error"):
            return sent
        reply = ime.reply_of(sent)
        error = _reply_error(reply)
        if error:
            return error
        decoded = decode(str(reply["dump"]))
        _record_events(serial, reply, decoded)
        return decoded
    return _failed(g.exc)


def reset_streaks(serial: str) -> None:
    """Forget both failure streaks of ``serial`` (a success, an enable or a restore)."""
    _FAILS.pop(serial, None)
    _NO_WINDOW.pop(serial, None)


def _strike(serial: str, error: str) -> None:
    streaks, cap = _FAILS, A11Y_MAX_CONSECUTIVE_FAILURES
    if _NO_WINDOW_REFUSAL in error:
        streaks, cap = _NO_WINDOW, A11Y_MAX_CONSECUTIVE_NO_WINDOW
    streaks[serial] = streaks.get(serial, 0) + 1
    if streaks[serial] >= cap:
        adb.clear_dump_provider(serial)


async def provider(serial: str) -> dict:
    """The dump provider ``adb.uiautomator_dump`` consults; drops itself after repeated failures.

    A ``no_window`` refusal counts on its own, longer streak: the service is alive,
    only the window is hidden. It is re-asked within a bounded budget
    (:mod:`tools.mobile.a11y_retry`); one that outlasts it falls back to
    uiautomator for that dump alone.
    """
    got = await a11y_retry.until_tree(fetch_dump, serial)
    if got.get("error"):
        _strike(serial, str(got["error"]))
        a11y_events.record(serial, None)  # a failed read leaves no stale clock
        return got
    reset_streaks(serial)
    ime_proof.on_dump(serial)
    return got

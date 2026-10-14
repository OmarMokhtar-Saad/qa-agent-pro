"""Input and URL leaves: tap/swipe/keyevent/open_url/pm_select (moved from adb.py)."""

from __future__ import annotations

import shlex
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from tools.mobile.adb_host import host


def _coord(value: Any) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    if 0 <= number <= host()._SWIPE_MAX:
        return number
    return None


async def tap(serial: str, x: object, y: object) -> dict:
    px, py = host()._coord(x), host()._coord(y)
    if px is None or py is None:
        return {"error": "Refusing to tap at " + repr((x, y)), "content": None}
    return await host().shell(serial, ["input", "tap", str(px), str(py)])


class Swipe(NamedTuple):
    x1: object
    y1: object
    x2: object
    y2: object
    ms: object = 300


async def swipe(serial: str, move: Swipe) -> dict:
    points = [host()._coord(v) for v in move[:4]]
    duration = host()._coord(move.ms)
    if any(p is None for p in points) or duration is None:
        return {
            "error": "Refusing to swipe with " + repr(tuple(move)),
            "content": None,
        }
    return await host().shell(
        serial, ["input", "swipe"] + [str(p) for p in points] + [str(duration)]
    )


async def keyevent(serial: str, code: object) -> dict:
    text = str(code or "")
    if not host().KEYEVENT_RE.match(text):
        return {"error": "Refusing keyevent " + repr(text[:40]), "content": None}
    return await host().shell(serial, ["input", "keyevent", text])


def _url_refusal(text: str) -> dict | None:
    """The refusal for a URL with a disallowed scheme or unsafe characters."""
    scheme = urlsplit(text).scheme.lower()
    if scheme not in host().ALLOWED_URL_SCHEMES:
        return {
            "error": (
                "Refusing to open a "
                + (scheme or "scheme-less")
                + " URL on the device; allowed schemes are "
                + ", ".join(host().ALLOWED_URL_SCHEMES)
                + "."
            ),
            "content": None,
        }
    if not host().SAFE_URL_RE.fullmatch(text):
        return {
            "error": "Refusing to open a URL containing unsafe characters.",
            "content": None,
        }
    return None


async def open_url(serial: str, url: str) -> dict:
    """Hand *url* to the device's VIEW intent (download links, Play Store)."""
    text = str(url or "").strip()
    refusal = _url_refusal(text)
    if refusal is not None:
        return refusal
    # adb joins argv with spaces and the device's sh re-parses it, so the URL
    # is quoted as one word (`?` and `#` are shell syntax there).
    return await host().shell(
        serial,
        ["am", "start", "-a", "android.intent.action.VIEW", "-d", shlex.quote(text)],
    )


def is_ime_id(text: str) -> bool:
    """Whether *text* has the shape :func:`pm_select` will send."""
    return bool(host().IME_ID_SHAPE.match(str(text or "")))


def _ime_refusal(result: dict) -> dict | None:
    """The refusal for an ``ime set`` that ran but did not take, else ``None``."""
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    combined = str(body.get("out") or "") + str(body.get("err") or "")
    if rc != 0 or host()._IME_REFUSAL_RE.search(combined):
        return {
            "error": "adb ime set failed: " + combined.strip()[:400],
            "content": None,
        }
    return None


# The name comes from the phase spec's module list. It is the ONE "make this
# component the active one" call the lane needs, and it is here rather than
# in ``ime.py`` so that every argv this lane builds is built in one file.
#
# Checks the device's actual answer: `adb shell ime set` can exit 0 while
# printing a refusal, so a non-zero ``rc`` OR a refusal phrase in the
# combined stdout/stderr is treated as a failure -- the same rc-plus-text
# pattern `adb.install` already uses.
async def pm_select(serial: str, ime_id: str) -> dict:
    """Select an input method by id (``adb shell ime set <id>``)."""
    text = str(ime_id or "")
    if not host().is_ime_id(text):
        return {
            "error": "Refusing to select input method " + repr(text[:60]) + ".",
            "content": None,
        }
    result = await host().shell(serial, ["ime", "set", text])
    if result.get("error"):
        return result
    return _ime_refusal(result) or result

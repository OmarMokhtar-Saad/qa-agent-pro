"""The adb shell side of the animation scales: read them, and build the put-back command."""

from __future__ import annotations

import logging
import re

from tools.mobile import adb

logger = logging.getLogger("tools.mobile.animations")

#: The three global settings Android's Developer options call "Window animation
#: scale", "Transition animation scale" and "Animator duration scale".
KEYS = (
    "window_animation_scale",
    "transition_animation_scale",
    "animator_duration_scale",
)

#: A value this module will WRITE back with ``settings put``. The previous values
#: are device output, so they are untrusted: anything that is not a plain number
#: is restored with ``settings delete`` (the Android default) instead of being
#: pasted into a shell command line.
_SCALE_RE = re.compile(r"\d{1,3}(\.\d{1,6})?")


def _is_zero(value: str) -> bool:
    return bool(_SCALE_RE.match(value)) and float(value) == 0.0


async def _run(serial: str, command: str) -> dict:
    """One ``adb shell`` round trip; a non-zero exit is an error, not success."""
    result = await adb.shell(str(serial), [command])
    if result.get("error"):
        return result
    body = result.get("content") or {}
    if int(body.get("rc") or 0) != 0:
        detail = (
            str(body.get("err") or "").strip() or str(body.get("out") or "").strip()
        )
        return {
            "error": "adb shell settings failed ("
            + (detail[:200] if detail else "exit " + str(body.get("rc")))
            + ")",
            "content": None,
        }
    return {"error": None, "content": str(body.get("out") or "")}


def _scales_from(output: str) -> dict:
    lines = [line.strip() for line in output.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) != len(KEYS):
        return {
            "error": "unexpected animation settings output ("
            + str(len(lines))
            + " lines for "
            + str(len(KEYS))
            + " keys)",
            "content": None,
        }
    return {"error": None, "content": dict(zip(KEYS, (v[:40] for v in lines)))}


async def read_scales(serial: str) -> dict:
    """``{key: value}`` for the three scales, in ONE shell call. Never raises."""
    try:
        result = await _run(
            serial, "; ".join("settings get global " + key for key in KEYS)
        )
        if result.get("error"):
            return result
        return _scales_from(str(result["content"]))
    except Exception as exc:
        logger.exception("mobile.animations.read_scales failed")
        return {"error": str(exc), "content": None}


def _restore_command(previous: dict) -> str:
    parts = []
    for key in KEYS:
        value = str((previous or {}).get(key) or "")
        if _SCALE_RE.fullmatch(value):
            parts.append("settings put global " + key + " " + value)
        else:
            parts.append("settings delete global " + key)
    return " && ".join(parts)

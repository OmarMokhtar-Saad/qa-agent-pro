"""Reading and writing the accessibility settings on the device."""

from __future__ import annotations

import re

from tools.mobile import adb, ime

SETTING = "enabled_accessibility_services"
MASTER = "accessibility_enabled"

#: The longest enabled_accessibility_services value this module will rewrite.
A11Y_MAX_SERVICES_VALUE_CHARS = 1000

_VALUE_RE = re.compile("[A-Za-z0-9_.$/:-]{1,%d}" % A11Y_MAX_SERVICES_VALUE_CHARS)


def component() -> str:
    """``package/package.QaA11yService`` for the pinned APK, or ``""``."""
    content = (ime.manifest() or {}).get("content") or {}
    package = str(content.get("package") or "")
    return package + "/" + package + ".QaA11yService" if package else ""


async def _sh(serial: str, command: str) -> dict:
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
            + (detail[:200] or "exit " + str(body.get("rc")))
            + ")",
            "content": None,
        }
    return {"error": None, "content": str(body.get("out") or "")}


def _norm(value: object) -> str:
    text = str(value or "").strip()
    return "" if text == "null" else text[: A11Y_MAX_SERVICES_VALUE_CHARS + 1]


def _items(value: str) -> list:
    return [part for part in str(value).split(":") if part]


async def read_state(serial: str) -> dict:
    """``(services, master)`` as device strings, in ONE shell call."""
    result = await _sh(
        serial,
        "settings get secure " + SETTING + "; settings get secure " + MASTER,
    )
    if result.get("error"):
        return result
    lines = str(result["content"]).splitlines()
    lines = (lines + ["", ""])[:2] if len(lines) < 2 else lines[:2]
    return {"error": None, "content": (_norm(lines[0]), _norm(lines[1]))}


def _restore_command(services: str, master):
    """The shell line giving the recorded values back, or ``None`` when unsafe.

    A *master* of ``None`` leaves ``accessibility_enabled`` alone: another service is
    enabled and the switch is theirs.
    """
    if services and not _VALUE_RE.fullmatch(services):
        return None
    if services:
        first = "settings put secure " + SETTING + " '" + services + "'"
    else:
        first = "settings delete secure " + SETTING
    if master is None:
        return first
    if master in ("0", "1"):
        second = "settings put secure " + MASTER + " " + master
    else:
        second = "settings delete secure " + MASTER
    return first + " && " + second


def append_service(items: list, ours: str) -> str:
    """The ``enabled_accessibility_services`` value with *ours* added after the tester's."""
    return ":".join(list(items) + [ours])

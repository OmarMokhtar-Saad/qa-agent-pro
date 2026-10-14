"""Device-scoped adb calls: ``device``, ``shell``, ``getprop`` (moved from adb.py)."""

from __future__ import annotations

import re

from tools.mobile.adb_consts import DEFAULT_TIMEOUT_S
from tools.mobile.adb_host import host
from tools.mobile.adb_stdin import StdinFeed


async def device(
    serial: str, args: list[str], timeout: int = DEFAULT_TIMEOUT_S
) -> dict:
    return await host()._device_in(serial, args, StdinFeed(None, timeout))


async def shell(serial: str, args: list[str], timeout: int = DEFAULT_TIMEOUT_S) -> dict:
    """``adb -s <serial> shell <args>``; stdin senders use :func:`shell_in`."""
    return await host().device(serial, ["shell"] + [str(a) for a in args], timeout)


async def getprop(serial: str, name: str) -> dict:
    """One system property as a stripped string."""
    if not re.match(r"^[A-Za-z0-9._-]{1,80}$", str(name or "")):
        return {
            "error": "Refusing to read property " + repr(str(name)[:40]),
            "content": None,
        }
    result = await host().shell(serial, ["getprop", str(name)])
    if result.get("error"):
        return result
    return {
        "error": None,
        "content": str((result["content"] or {}).get("out") or "").strip(),
    }

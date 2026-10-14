"""App-log evidence transport: logcat, pidof and the device clock (moved from adb.py).

Every function here is TRANSPORT, gated by the lifecycle function that calls it
(``tools/mobile_evidence/capture.py`` reads both flags before any of these).
Two of them are named in tests/mobile/test_mobile_killswitch_surface.py's
EFFECT_CALLS so a FUTURE public function in this package that calls them
directly is seen by the scan.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile.adb_consts import MAX_DUMP_BYTES
from tools.mobile.adb_host import host


async def logcat_clear(serial: str) -> dict:
    """``adb -s <serial> logcat -c``: empty the ring buffer before a case starts."""
    return await host().device(serial, ["logcat", "-c"])


def _pid_flag(pid: Any) -> str | None:
    try:
        number = int(pid)
    except (TypeError, ValueError, OverflowError):
        return None
    return "--pid=" + str(number) if number > 0 else None


def _logcat_args(tag: str, pid: int | None) -> tuple[list[str], str | None]:
    """The logcat argv for *tag*/*pid*, and the refusal text when one is bad."""
    args = ["logcat", "-d", "-v", "threadtime"]
    if pid is not None:
        flag = _pid_flag(pid)
        if flag is None:
            return args, "Refusing logcat pid " + repr(pid)[:40]
        args.append(flag)
    if tag:
        if not host()._LOGCAT_TAG_RE.match(str(tag)):
            return args, "Refusing logcat tag " + repr(str(tag)[:40])
        args += ["-s", str(tag)]
    return args, None


class LogQuery(NamedTuple):
    tag: str = ""
    pid: int | None = None
    max_bytes: int = MAX_DUMP_BYTES


# ``{"error", "content": {"text", "truncated"}}``. The text is never logged: a
# logcat slice is the app's own output and may carry anything the app printed.
async def logcat_dump(serial: str, query: LogQuery | None = None) -> dict:
    """``logcat -d -v threadtime``, narrowed to *tag* and/or *pid*, byte-capped."""
    query = LogQuery() if query is None else query
    args, refusal = _logcat_args(query.tag, query.pid)
    if refusal is not None:
        return {"error": refusal, "content": None}
    result = await host().device(serial, args, timeout=host().DUMP_TIMEOUT_S)
    if result.get("error"):
        return result
    out = str((result["content"] or {}).get("out") or "")
    text, truncated = host()._capped(out, query.max_bytes)
    return {"error": None, "content": {"text": text, "truncated": truncated}}


async def pidof(serial: str, package: str) -> int | None:
    """The app's pid, or None when it is not running or the name is refused."""
    if not host().valid_package_name(package):
        return None
    result = await host().shell(serial, ["pidof", "-s", str(package)])
    if result.get("error"):
        return None
    out = str((result["content"] or {}).get("out") or "").strip().split()
    if not out or not out[0].isdigit():
        return None
    number = int(out[0])
    return number if number > 0 else None


async def device_epoch_ms(serial: str) -> int | None:
    """The device's wall clock as epoch milliseconds, or None.

    ``date +%s%3N`` on a toybox that lacks ``%N`` prints the literal, which is
    not thirteen digits and therefore reads as None -- never a guessed zero.
    """
    result = await host().shell(serial, ["date", "+%s%3N"])
    if result.get("error"):
        return None
    text = str((result["content"] or {}).get("out") or "").strip()
    return int(text) if host()._EPOCH_MS_RE.match(text) else None

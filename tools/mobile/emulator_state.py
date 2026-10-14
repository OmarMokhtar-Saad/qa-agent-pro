"""Process-memory records of the emulators this process started, and the SDK lookup.

``PROCS`` and ``RUNS`` live in the ``emulator`` facade (tests patch them there);
this leaf reaches them, and the bounds, through ``host()`` at call time.
"""

from __future__ import annotations

import logging
import time

from config.settings_guard import never_raise
from tools.mobile import paths, sdk_locator
from tools.mobile.emulator_host import host

logger = logging.getLogger("tools.mobile.emulator")

_STARTED: dict[str, float] = {}


def emulator_binary() -> str:
    """The SDK's ``emulator`` binary, or ``""`` when no SDK is installed."""
    located = (sdk_locator.locate_sdk() or {}).get("content") or {}
    return str((located.get("tools") or {}).get("emulator") or "")


def note_started(avd: str, *, now: float | None = None) -> None:
    """Record that *avd* was spawned just now. Never raises."""
    with never_raise(logger, "mobile.emulator.note_started failed", exc_info=True):
        name = str(avd or "").strip()[:120]
        if not name:
            return
        _STARTED[name] = time.monotonic() if now is None else float(now)
        while len(_STARTED) > host().MAX_BOOT_RECORDS:
            _STARTED.pop(min(_STARTED, key=_STARTED.__getitem__), None)


def _live_started(stamp: float) -> list[dict]:
    """Drop the records older than the TTL and return the rest, oldest first."""
    for name in [
        n for n, t in _STARTED.items() if stamp - t > host().BOOT_RECORD_TTL_S
    ]:
        _STARTED.pop(name, None)
    return [
        {"avd": name, "age_s": max(0, int(stamp - started))}
        for name, started in sorted(_STARTED.items(), key=lambda kv: kv[1])
    ]


# Two kinds of reader: one that found the device list empty and wants to say an
# emulator is still booting, and ``avd_manage``'s delete guard, which reads it
# unconditionally so an AVD that was just started is not removed. Never raises.
def recently_started(*, now: float | None = None) -> list[dict]:
    """``[{"avd", "age_s"}]`` for spawns younger than the TTL, oldest first."""
    with never_raise(logger, "mobile.emulator.recently_started failed", exc_info=True):
        return _live_started(time.monotonic() if now is None else float(now))
    return []


def clear_started() -> None:
    """Forget every record. For tests."""
    _STARTED.clear()
    host().RUNS.clear()
    host().PROCS.clear()


def _emulator_log_path(avd: str) -> str:
    """``state/emulator-<avd>.log``, or ``""`` when the tree is unavailable.

    There is no run folder yet when an emulator is spawned, so the log lives in
    the shared ``state/`` dir; the file is truncated on every spawn (see
    ``emulator.spawn``).
    """
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(avd))[:60]
    with never_raise(logger, "mobile.emulator: no log path", exc_info=True):
        return str(paths.state_file("emulator-" + safe + ".log"))
    return ""

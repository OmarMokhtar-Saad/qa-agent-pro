"""Wait for a clip's recorder to open its output file before it is stopped."""

from __future__ import annotations

import time

from tools.mobile import adb
from tools.mobile.media_consts import MAX_STOP_WAIT_S, RECORDER_OPEN_S


def age_s(held: dict) -> float | None:
    """Seconds since the recorder spawned, or None for a handle without a start time."""
    t0 = held.get("t0")
    return time.monotonic() - t0 if isinstance(t0, (int, float)) else None


def lived_short(held: dict) -> bool:
    """True when the step ended before the recorder had its open-file window."""
    age = age_s(held)
    return age is not None and age < RECORDER_OPEN_S


def _exited(held: dict) -> bool:
    return getattr(held.get("proc"), "returncode", None) is not None


async def file_open(serial: str, held: dict) -> None:
    """Return once the clip is non-empty, the recorder exited, the window ended or a probe failed.

    Records ``short_step`` on the handle: the step ended inside the open-file window.
    """
    remote = str(held.get("remote") or "")
    held["short_step"] = lived_short(held)
    while remote and not _exited(held):
        age = age_s(held)
        if age is None or age >= RECORDER_OPEN_S:
            return
        wait = min(MAX_STOP_WAIT_S, max(1, int(RECORDER_OPEN_S - age + 0.999)))
        done = await adb.shell(serial, ["test", "-s", remote], wait)
        if done.get("error"):
            return
        body = done.get("content")
        if isinstance(body, dict) and not body.get("rc"):
            return

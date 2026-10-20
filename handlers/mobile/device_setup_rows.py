"""``qa_mobile_status`` rows for a device BEING SET UP by this server (fix/1520).

The run listing reads run dirs, and a pre-run hold has no run dir yet: a
device held under a provisioning label was invisible to ``qa_mobile_status``
for the whole setup call (205 s on 2026-10-20, then for ever once the host had
cancelled the call and the hold outlived it). These rows come from this
process's own fd table (``locks._HELD``), the one place that hold exists, so
the status reply can say what ``qa_mobile_test`` would refuse on.

Per process on purpose: another server's pre-run hold is its own to show.
"""

from __future__ import annotations

import os
import time

from tools.mobile import locks, prerun_inflight

_RUNNING_TAIL = "; its setup call is still running"
_STALE_TAIL = (
    "; its setup call has ended, so the hold is stale and is given back on "
    "the next `qa_mobile_test`"
)
_OTHER_TAIL = (
    "; held by another tool call in this server (a capture setup or "
    "teardown, not a `qa_mobile_test`), which gives it back when it ends"
)


def _tail(owner: str) -> str:
    if prerun_inflight.is_inflight(owner):
        return _RUNNING_TAIL
    if prerun_inflight.has_ended(owner):
        return _STALE_TAIL
    return _OTHER_TAIL


def _device_of(name: str) -> str:
    """The readable serial inside a device lock *name*."""
    body = name[len(locks.DEVICE_LOCK_PREFIX) :]
    return body.rsplit("-", 1)[0] or body


def _held_prerun() -> list[tuple[str, str, float]]:
    """``(lock name, owner, since)`` for every pre-run device hold in this process."""
    with locks._MUTEX:
        return [
            (name, held.owner, held.since)
            for name, held in locks._HELD.items()
            if name.startswith(locks.DEVICE_LOCK_PREFIX)
            and locks.is_provisioning_owner(held.owner)
        ]


def _setup_row(entry: tuple[str, str, float], now: float) -> str:
    """One status line for a pre-run hold."""
    name, owner, since = entry
    tail = _tail(owner)
    return (
        "- Setting up (no run id yet) on `"
        + _device_of(name)
        + "` — owner `"
        + owner
        + "`, this server (pid "
        + str(os.getpid())
        + "), "
        + str(max(0, int(now - since)))
        + " s old"
        + tail
    )


def setup_rows(now: float | None = None) -> list[str]:
    """One line per device this server is setting up right now (often none)."""
    at = time.time() if now is None else float(now)
    return [_setup_row(entry, at) for entry in sorted(_held_prerun())]

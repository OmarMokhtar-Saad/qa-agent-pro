"""Wall time of the phases that run BEFORE the run exists (App Tester, home
check, install, launch), so the run's first step_timings row can ledger them.

``step_timing`` is armed only once the first case starts, so these phases were
a blind spot (an 81 s start showed no time at all). They are recorded here per
device serial, then ``replay_into_ledger`` drains them into the armed
breakdown as ``prerun:<phase>`` rows.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable

from tools.mobile import step_timing

#: Rows kept per serial; a start has a handful of phases.
MAX_PRERUN_ROWS = 12

_ROWS: dict = {}


async def timed(serial: str, name: str, awaitable: Awaitable[Any]) -> Any:
    """Await *awaitable* and record its wall time under *name* for *serial*."""
    start = time.monotonic()
    try:
        return await awaitable
    finally:
        rows = _ROWS.setdefault(str(serial), [])
        if len(rows) < MAX_PRERUN_ROWS:
            rows.append((name, int((time.monotonic() - start) * 1000)))


def forget(serial: str) -> None:
    """Drop the rows of an earlier start (a new start begins clean)."""
    _ROWS.pop(str(serial), None)


def rows_of(serial: str) -> list:
    """The ``(phase, ms)`` rows recorded for *serial* so far."""
    return list(_ROWS.get(str(serial), []))


def replay_into_ledger(serial: str) -> None:
    """Ledger the pre-run phases of *serial* into the armed step breakdown."""
    for name, ms in _ROWS.pop(str(serial), []):
        step_timing.note("prerun:" + name, ms)


def replay_all_into_ledger() -> None:
    """Ledger every recorded device's pre-run phases (the explore lane arms the
    step before it knows its serial; one start drains one device in practice)."""
    for serial in list(_ROWS):
        replay_into_ledger(serial)

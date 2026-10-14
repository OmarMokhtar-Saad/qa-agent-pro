"""How long THIS device takes to dump its screen, and what a wait may assume.

Fix round 3, items 2 and 4. On a heavy AVD behind an overloaded host one dump
took longer than a Pixel-class floor, so the last poll of a wait landed after
its deadline. This module keeps a rolling p90 of REAL dump times per serial
(:func:`p90_ms`) for the slow-host warning; since S17 a wait no longer keeps a
margin at all -- each poll's dump is cut at the wait's deadline instead.

It also owns the ONE dump in flight per serial. Every dump writes the same
``adb.DUMP_REMOTE_PATH``, so a dump cut by a wait's deadline must not be
followed by a second dump on that device while the first is still writing the
file. :func:`start` runs the dump as a task the caller awaits through
``asyncio.shield``: a cut cancels only the caller's await, the task runs to the
end and records its real time, and :func:`join` makes the next dump wait for it.

Process-global and bounded: :data:`MAX_SERIALS` devices, :data:`SAMPLE_COUNT`
samples each, :data:`MAX_WARNED_RUNS` run ids remembered for the once-per-run
host warning. In memory only -- a restart forgets, which is the right answer
for a latency that moves with the host's load.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import collections  # noqa: F401
import logging  # noqa: F401
import math  # noqa: F401
import time  # noqa: F401
from typing import Any, Awaitable  # noqa: F401

from config.settings_guard import never_raise
from tools.mobile import avd_guard
from tools.mobile.dump_latency_run import (  # noqa: F401
    _left,
    _on_done,
    _timed,
    inflight,
    join,
    start,
)
from tools.mobile.dump_latency_state import (  # noqa: F401
    _INFLIGHT,
    _SAMPLES,
    _WARNED,
    MAX_SERIALS,
    SAMPLE_COUNT,
    _record,
    describe,
    p90_ms,
    reset,
)

logger = logging.getLogger(__name__)

#: Run ids remembered as already warned, so the host warning is said once per
#: run and this set cannot grow with the process's lifetime. Bounded from above
#: in ``tests/mobile/test_mobile_bounds_upper.py``.
MAX_WARNED_RUNS = 256

#: Load average per core above which a step reply says the HOST is the
#: bottleneck. Twice ``avd_guard.HEAVY_HOST_LOAD_PER_CORE_MAX``: that one is
#: advice before a run starts, this one interrupts a run, so it waits for worse.
HOST_WARN_LOAD_PER_CORE = 2.0

#: p90 dump time, in ms, above which a step reply says the DEVICE is slow.
SLOW_DUMP_WARN_MS = 5000


def _reasons(serial: str) -> list[str]:
    """Why this step's reply should warn: the host load, a slow device, or neither."""
    reasons: list[str] = []
    with never_raise() as g:
        load = avd_guard.host_load()
    if g.failed:
        load = None
    if load is not None and load > HOST_WARN_LOAD_PER_CORE:
        reasons.append(f"the host's load is {load:.1f} per CPU core")
    p90 = p90_ms(serial)
    if p90 is not None and p90 > SLOW_DUMP_WARN_MS:
        reasons.append(f"one screen dump takes {p90 / 1000:.1f} s on this device")
    return reasons


def _warning_text(reasons: list[str]) -> str:
    """The advice sentence naming *reasons*."""
    return (
        "Slow device: "
        + " and ".join(reasons)
        + ". Every wait now keeps a margin that fits it, so steps take longer. "
        + "Switch to a 1080p Pixel AVD (e.g. Pixel 6, 1080x2400) and close Screen "
        + "Sharing and any other heavy program on the host."
    )


# The slow-host / slow-device warning, said at most once per run.
#
# Empty when neither threshold is crossed, and empty on every later call for
# a run already warned. Advice only: nothing here refuses or slows a step.
def host_warning_once(run_id: object, serial: str) -> str:
    """The slow-host / slow-device warning, at most once per run, else ``""``."""
    reasons = _reasons(serial)
    if not reasons:
        return ""
    key = str(run_id or "")
    if key in _WARNED:
        return ""
    _WARNED[key] = None
    while len(_WARNED) > MAX_WARNED_RUNS:
        _WARNED.popitem(last=False)
    return _warning_text(reasons)

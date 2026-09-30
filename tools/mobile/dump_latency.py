"""How long THIS device takes to dump its screen, and what a wait may assume.

Fix round 3, items 2 and 4. ``executor.MIN_POLL_MARGIN_MS`` is a FLOOR written
for a Pixel-class AVD; on a heavy AVD behind an overloaded host one dump took
longer than that floor, so the last poll of a wait landed after its deadline.
This module keeps a rolling p90 of REAL dump times per serial, and the margin a
polled wait keeps is the larger of the floor and that p90 (:func:`margin_ms`).

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

import asyncio
import collections
import logging
import math
import time
from typing import Any, Awaitable

from tools.mobile import avd_guard

logger = logging.getLogger(__name__)

#: Dump times kept per serial for the p90. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
SAMPLE_COUNT = 20

#: Devices whose samples are kept; the least recently dumped is forgotten
#: first. Bounded from above in ``tests/mobile/test_mobile_bounds_upper.py``.
MAX_SERIALS = 16

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

_SAMPLES: collections.OrderedDict = collections.OrderedDict()
_INFLIGHT: dict = {}
_WARNED: collections.OrderedDict = collections.OrderedDict()


def _record(serial: str, seconds: float) -> None:
    key = str(serial or "")
    samples = _SAMPLES.pop(key, None)
    if samples is None:
        samples = collections.deque(maxlen=SAMPLE_COUNT)
    samples.append(max(0.0, float(seconds)))
    _SAMPLES[key] = samples
    while len(_SAMPLES) > MAX_SERIALS:
        _SAMPLES.popitem(last=False)


def p90_ms(serial: str) -> int | None:
    """Nearest-rank p90 of this serial's recent dumps in ms, or ``None``."""
    samples = _SAMPLES.get(str(serial or ""))
    if not samples:
        return None
    ordered = sorted(samples)
    index = max(0, math.ceil(0.9 * len(ordered)) - 1)
    return int(ordered[index] * 1000)


def margin_ms(serial: str, floor: int) -> int:
    """The larger of *floor* and this device's p90 dump time, in ms."""
    return max(int(floor), p90_ms(serial) or 0)


async def _timed(serial: str, awaitable: Awaitable[Any]) -> Any:
    started = time.monotonic()
    try:
        result = await awaitable
    except asyncio.CancelledError:
        raise
    except Exception:
        _record(serial, time.monotonic() - started)
        raise
    _record(serial, time.monotonic() - started)
    return result


def start(serial: str, awaitable: Awaitable[Any]) -> asyncio.Task:
    """Run *awaitable* as THIS serial's one dump in flight; return its task.

    The caller awaits it through ``asyncio.shield``, so a deadline cancels the
    caller's wait and never the dump. Call :func:`join` first: starting a
    second dump beside a running one is exactly what this module stops.
    """
    key = str(serial or "")
    task = asyncio.ensure_future(_timed(key, awaitable))
    _INFLIGHT[key] = task

    def _done(finished: asyncio.Future, key: str = key) -> None:
        if _INFLIGHT.get(key) is finished:
            _INFLIGHT.pop(key, None)
        if not finished.cancelled():
            # Retrieved, so a cut dump that later failed is not logged as an
            # exception nobody looked at. The next dump reports the device.
            finished.exception()

    task.add_done_callback(_done)
    return task


def inflight(serial: str) -> asyncio.Task | None:
    """This serial's dump still running on THIS event loop, or ``None``."""
    key = str(serial or "")
    task = _INFLIGHT.get(key)
    if task is None:
        return None
    try:
        same_loop = task.get_loop() is asyncio.get_running_loop()
    except RuntimeError:
        same_loop = False
    if task.done() or not same_loop:
        _INFLIGHT.pop(key, None)
        return None
    return task


async def join(serial: str, timeout: float | None = None) -> bool:
    """Wait until NO dump is in flight on this serial. True when none is left.

    Loops, because several callers can wait on the same dump: when it ends,
    the first one woken starts the next dump before the others run, and they
    must then wait for THAT one. A caller that gets True reaches :func:`start`
    with no ``await`` in between, so on one event loop two dumps never
    overlap. Never raises the dump's own error and never cancels it:
    ``asyncio.wait`` leaves the task alone when its caller is cancelled or
    times out.
    """
    loop = asyncio.get_running_loop()
    end = None if timeout is None else loop.time() + timeout
    while True:
        task = inflight(serial)
        if task is None:
            return True
        left = None if end is None else end - loop.time()
        if left is not None and left <= 0:
            return False
        done, _ = await asyncio.wait({task}, timeout=left)
        if not done:
            return False


def reset() -> None:
    """Forget every sample, dump in flight and warned run. For tests."""
    for task in list(_INFLIGHT.values()):
        try:
            if not task.done():
                task.cancel()
        except RuntimeError:  # its loop is already closed
            pass
    _INFLIGHT.clear()
    _SAMPLES.clear()
    _WARNED.clear()


def describe(serial: str) -> str:
    """One line naming this device's dump p90, or ``""`` before any dump."""
    samples = _SAMPLES.get(str(serial or ""))
    if not samples:
        return ""
    return (
        "screen dump p90 "
        + f"{p90_ms(serial) or 0:,}"
        + " ms over the last "
        + str(len(samples))
        + " on this device"
    )


def host_warning_once(run_id: object, serial: str) -> str:
    """The slow-host / slow-device warning, said at most once per run.

    Empty when neither threshold is crossed, and empty on every later call for
    a run already warned. Advice only: nothing here refuses or slows a step.
    """
    reasons: list[str] = []
    try:
        load = avd_guard.host_load()
    except Exception:
        load = None
    if load is not None and load > HOST_WARN_LOAD_PER_CORE:
        reasons.append(f"the host's load is {load:.1f} per CPU core")
    p90 = p90_ms(serial)
    if p90 is not None and p90 > SLOW_DUMP_WARN_MS:
        reasons.append(f"one screen dump takes {p90 / 1000:.1f} s on this device")
    if not reasons:
        return ""
    key = str(run_id or "")
    if key in _WARNED:
        return ""
    _WARNED[key] = None
    while len(_WARNED) > MAX_WARNED_RUNS:
        _WARNED.popitem(last=False)
    return (
        "Slow device: "
        + " and ".join(reasons)
        + ". Every wait now keeps a margin that fits it, so steps take longer. "
        + "Switch to a 1080p Pixel AVD (e.g. Pixel 6, 1080x2400) and close Screen "
        + "Sharing and any other heavy program on the host."
    )

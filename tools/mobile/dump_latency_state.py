"""The per-serial dump samples, the dumps in flight and the runs already warned."""

from __future__ import annotations

import collections
import math

#: Dump times kept per serial for the p90. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
SAMPLE_COUNT = 20

#: Devices whose samples are kept; the least recently dumped is forgotten
#: first. Bounded from above in ``tests/mobile/test_mobile_bounds_upper.py``.
MAX_SERIALS = 16

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


# Callbacks run just before every timed dump, by serial. A module that needs a
# reading beside each dump registers here, so the dump path imports nothing of it.
BEFORE_DUMP: list = []


def before_dump(serial: str) -> None:
    """Run every registered before-dump callback for *serial*."""
    for hook in BEFORE_DUMP:
        hook(serial)

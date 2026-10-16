"""The per-serial dump samples, the dumps in flight and the runs already warned."""

from __future__ import annotations

import collections
import collections.abc
import math

from tools.mobile import step_timing

#: Dump times kept per serial for the p90. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
SAMPLE_COUNT = 20

#: Devices whose samples are kept; the least recently dumped is forgotten
#: first. Bounded from above in ``tests/mobile/test_mobile_bounds_upper.py``.
MAX_SERIALS = 16

#: Sources kept apart per device (a11y, uiautomator fallback, ...); more are dropped.
MAX_SOURCES = 4

_SAMPLES: collections.OrderedDict = collections.OrderedDict()
_BY_SOURCE: dict = {}
_LAST_SOURCE: dict = {}
_INFLIGHT: dict = {}
_WARNED: collections.OrderedDict = collections.OrderedDict()


def _append(table: dict, key: str, seconds: float) -> None:
    samples = table.get(key)
    if samples is None:
        samples = table[key] = collections.deque(maxlen=SAMPLE_COUNT)
    samples.append(seconds)


def _record(serial: str, seconds: float, source: str = "") -> None:
    key = str(serial or "")
    samples = _SAMPLES.pop(key, None)
    if samples is None:
        samples = collections.deque(maxlen=SAMPLE_COUNT)
    samples.append(max(0.0, float(seconds)))
    _SAMPLES[key] = samples
    sources = _BY_SOURCE.setdefault(key, {})
    if source in sources or len(sources) < MAX_SOURCES:
        _append(sources, source, samples[-1])
        if source:  # a failed dump names none; keep the last real one
            _LAST_SOURCE[key] = source
    while len(_SAMPLES) > MAX_SERIALS:
        old, _gone = _SAMPLES.popitem(last=False)
        _BY_SOURCE.pop(old, None)
        _LAST_SOURCE.pop(old, None)


def _p90(samples: collections.abc.Iterable[float]) -> int | None:
    ordered = sorted(samples)
    if not ordered:
        return None
    index = max(0, math.ceil(0.9 * len(ordered)) - 1)
    return int(ordered[index] * 1000)


def last_source(serial: str) -> str:
    """The source of this serial's latest dump, or ``""`` when unknown."""
    return _LAST_SOURCE.get(str(serial or ""), "")


def p90_ms(serial: str, source: str | None = None) -> int | None:
    """Nearest-rank p90 of this serial's recent dumps in ms, or ``None``.

    With *source* only that source's dumps count; without it every dump does.
    """
    key = str(serial or "")
    if source is None:
        return _p90(_SAMPLES.get(key) or ())
    return _p90(_BY_SOURCE.get(key, {}).get(source) or ())


def next_dump_p90(serial: str) -> int | None:
    """The p90 of the source the next dump most likely uses (the last one's)."""
    own = p90_ms(serial, last_source(serial))
    return own if own is not None else p90_ms(serial)


def note_p90(serial: str) -> None:
    """Ledger the p90 the anchor-probe skip reads, named by its source."""
    ms = next_dump_p90(serial)
    if ms is not None:
        label = last_source(serial) or "any"
        step_timing.note(("p90:" + label)[:40], ms)


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
    _BY_SOURCE.clear()
    _LAST_SOURCE.clear()
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

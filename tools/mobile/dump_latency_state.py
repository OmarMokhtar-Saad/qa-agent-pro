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
#: Dumps of the slow uiautomator fallback (it waits for 1 s of no events, up to
#: 10 s): kept out of the all-source p90 so one cannot stretch every deadline.
_UIA: dict = {}
_SLOW: set = set()
#: Slow samples needed before they stand in for an empty all-source p90.
UIA_MIN_SAMPLES = 3
_SLOW_SOURCE = "uiautomator-dump"
_WARNED: collections.OrderedDict = collections.OrderedDict()


#: Seconds of a confirmation dump run INSIDE the dump being timed, per serial.
_ASIDE: dict = {}


def _append(table: dict, key: str, seconds: float) -> None:
    samples = table.get(key)
    if samples is None:
        # The first dump of a source is a warm-up (cold service, cold
        # cache): count it nowhere, it would lift the p90 for 20 dumps.
        table[key] = collections.deque(maxlen=SAMPLE_COUNT)
        return
    samples.append(seconds)


def record_aside(serial: str, seconds: float, source: str) -> None:
    """Time a dump that ran INSIDE the dump being timed (a freshness confirm).

    It is kept under its own *source* and taken out of the enclosing dump's
    time, so it never reads as that dump's latency.
    """
    key = str(serial or "")
    sources = _BY_SOURCE.setdefault(key, {})
    if source in sources or len(sources) < MAX_SOURCES:
        _append(sources, source, max(0.0, float(seconds)))
    _ASIDE[key] = _ASIDE.get(key, 0.0) + max(0.0, float(seconds))


def take_aside(serial: str) -> float:
    """Seconds recorded by :func:`record_aside` since the last take; resets."""
    return _ASIDE.pop(str(serial or ""), 0.0)


def mark_slow(serial: str, real: bool = True) -> None:
    """The dump now running is a real uiautomator run (*real*): its sample is
    not an "any" one. A confirm read (``use_provider=False``) passes False."""
    if real:
        _SLOW.add(str(serial or ""))


def _bucket(key: str, source: str) -> collections.deque:
    """The deque this sample counts in: slow ones apart from the all-source p90."""
    slow = key in _SLOW or source == _SLOW_SOURCE
    _SLOW.discard(key)
    table = _UIA if slow else _SAMPLES
    samples = table.pop(key, None)
    if samples is None:
        samples = collections.deque(maxlen=SAMPLE_COUNT)
    table[key] = samples
    return samples


def _record(serial: str, seconds: float, source: str = "") -> None:
    key = str(serial or "")
    samples = _bucket(key, source)
    samples.append(max(0.0, float(seconds)))
    sources = _BY_SOURCE.setdefault(key, {})
    if source in sources or len(sources) < MAX_SOURCES:
        _append(sources, source, samples[-1])
        if source:  # a failed dump names none; keep the last real one
            _LAST_SOURCE[key] = source
    _evict(_SAMPLES)
    _evict(_UIA)  # a serial with only slow samples must age out too


def _evict(table: dict) -> None:
    """Drop the least recently sampled serials of *table* past MAX_SERIALS;
    their source notes go once neither table holds them."""
    while len(table) > MAX_SERIALS:
        old = next(iter(table))
        table.pop(old)
        if old not in _SAMPLES and old not in _UIA:
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
        slow = _UIA.get(key) or ()
        return _p90(_SAMPLES.get(key) or (slow if len(slow) >= UIA_MIN_SAMPLES else ()))
    return _p90(_BY_SOURCE.get(key, {}).get(source) or ())


def next_dump_p90_labelled(serial: str) -> tuple[str, int | None]:
    """``(bucket, ms)`` of the p90 the next dump is judged by.

    The bucket is the last dump's source; ``"any"`` when that bucket is empty and the
    all-sample p90 answered instead. ``ms`` is ``None`` when nothing is recorded.
    """
    source = last_source(serial)
    own = p90_ms(serial, source) if source else None
    if own is not None:
        return source, own
    return "any", p90_ms(serial)


def next_dump_p90(serial: str) -> int | None:
    """The p90 of the source the next dump most likely uses (the last one's)."""
    return next_dump_p90_labelled(serial)[1]


def note_p90(serial: str) -> None:
    """Ledger the p90 the anchor-probe skip reads, named by the bucket it came from."""
    label, ms = next_dump_p90_labelled(serial)
    if ms is not None:
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
    _UIA.clear()
    _SLOW.clear()
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

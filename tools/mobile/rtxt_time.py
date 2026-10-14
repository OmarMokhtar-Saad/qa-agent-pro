"""Report durations, latency bands and stored-moment stamps."""

from __future__ import annotations

import time
from typing import Any

#: The same three bands the reference report uses for a case's wall clock.
LAT_BUCKETS = (
    ("fast", "under 3s", 3000),
    ("ok", "3-8s", 8000),
    ("slow", "over 8s", None),
)


def _ms(value: Any) -> int | None:
    """A duration from a store field, or None when it is not a measurement.

    A ZERO is not a measurement either. The runner writes ``ms: 0`` for an action
    it did not time -- a 4000 ms wait carries it -- and a bar of width zero would
    be a measurement of zero, which is the one thing this page must not draw.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number <= 0 or number != number:  # NaN
        return None
    return int(min(number, 10**9))


def fmt_ms(ms: object) -> str:
    """``412 ms`` under a second, ``2.3s`` at a second and over, ``1.4m`` past a minute."""
    number = _ms(ms)
    if number is None:
        return "—"
    if number < 1000:
        return str(number) + " ms"
    if number < 60000:
        return "%.1fs" % (number / 1000.0)
    return "%.1fm" % (number / 60000.0)


def _lat_bucket(ms: object) -> str:
    number = _ms(ms)
    if number is None:
        return ""
    for key, _label, ceiling in LAT_BUCKETS:
        if ceiling is None or number < ceiling:
            return key
    return LAT_BUCKETS[-1][0]


def _percentile(values: list, share: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * share))))
    return int(ordered[index])


# THE WHOLE CALL IS INSIDE THE GUARD, and the reason is a measured page loss,
# not tidiness. The coercion was guarded and ``localtime`` was not, so a
# manifest carrying a non-finite ``created`` -- ``json.loads`` accepts a bare
# ``Infinity`` -- raised ``OverflowError`` out of ``_facts_strip``, through
# ``_document``, into ``render``'s outer ``except``: NO PAGE AT ALL, over one
# cell. A helper that promises "any moment" must be total for any moment, and
# ``"unknown"`` is what it already returns for a moment it cannot read, so no
# caller sees a new shape.
#
# ``OSError`` joins the tuple because that is what ``localtime`` raises for an
# out-of-range (rather than non-finite) value on some platforms, and
# ``OverflowError`` must stay in it for `tests/test_coercion_guards.py`, whose
# ``blind_guards`` rule rejects a handler whose names are a subset of
# ``{TypeError, ValueError}``.
def _stamp(value: Any) -> str:
    """A stored moment as ``YYYY-MM-DD HH:MM:SS``, or ``"unknown"``."""
    try:
        moment = float(value or 0)
        if moment <= 0:
            return "unknown"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(moment))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def _short_stamp(value: Any) -> str:
    """The same moment, shorter, and total for the same reason.

    NOT reachable with a disk value today: both call sites pass ``time.time()``.
    It is hardened anyway because it is the same shape one function away, and
    the reachability that protects it is a property of its CALLERS -- the next
    one to pass a stored moment would reintroduce the defect this batch has now
    fixed twice. Being unreachable, this half has no mutant, which is stated
    rather than papered over with a kill it cannot earn.
    """
    try:
        moment = float(value or 0)
        if moment <= 0:
            return "unknown"
        return time.strftime("%d %b %Y %H:%M", time.localtime(moment))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def _epoch(value: Any) -> float | None:
    """An epoch SECONDS stamp from a store field, or ``None`` when it is not one.

    Deliberately NOT :func:`_ms`, which is a DURATION coercer: it clamps to ``10**9``, and
    every real epoch second passed ``10**9`` in 2001 -- so reading a timestamp through it
    pins every stamp on this page to the same instant in 2001, and the caller that then
    divided by 1000 moved the run to 1970. Two errors that agree, and a fixture whose
    ``updated`` is a small number (2.0, 300.0) cannot see either one. That is why
    ``test_a_real_epoch_stamp_is_not_clamped`` grades this with a REAL stamp.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number <= 0:
        return None
    return number


def last_checkpoint(cases: object) -> float | None:
    """The newest ``case["updated"]`` in epoch SECONDS, or ``None`` when none carries one."""
    stamps = []
    for case in cases if isinstance(cases, (list, tuple)) else ():
        value = _epoch(case.get("updated")) if isinstance(case, dict) else None
        if value is not None:
            stamps.append(value)
    return max(stamps) if stamps else None


#: The word the page uses for it, so no section invents its own.
LAST_CHECKPOINT_LABEL = "last checkpoint"


LAST_CHECKPOINT_SUB = "derived from the newest case checkpoint; this run recorded no end time, because nothing writes one and an abandoned run has no honest end"


def elapsed_note(manifest: object, cases: object) -> str:
    """Elapsed, or the NAME of whichever half is missing. Never a silent blank."""
    book = manifest if isinstance(manifest, dict) else {}
    created = _epoch(book.get("created"))
    end = last_checkpoint(cases)
    if created is None and end is None:
        return "no start time recorded, and no case has ever been checkpointed"
    if created is None:
        return "no start time recorded, so elapsed cannot be computed"
    if end is None:
        return "no case has been checkpointed yet, so elapsed cannot be computed"
    # Both halves are epoch SECONDS; fmt_ms wants milliseconds.
    return fmt_ms(max(0, int((end - created) * 1000.0)))

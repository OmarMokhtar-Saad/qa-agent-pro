"""Numbers on an exchange row: the call's elapsed time and its readable forms."""

from __future__ import annotations

from typing import Any, cast


def _duration(rec: dict) -> float | None:
    """The call's elapsed time: measured if the app measured it, else derived."""
    got = rec.get("durationMs")
    return got if got is not None else rec.get("derivedMs")


def dur_ms(ms: object) -> str:
    """Sub-second work keeps its milliseconds; a second and over is divided for the reader."""
    if ms is None:
        return ""
    try:
        number = float(cast("Any", ms))
        # Inside the guard: `round` is a coercer too, and a value the
        # guard just admitted may be infinite.
        return "%d ms" % round(number) if number < 1000 else "%.1fs" % (number / 1000.0)
    except (TypeError, ValueError, OverflowError):
        return ""


def fmt_ms(ms: object) -> str:
    if ms is None:
        return "\u2014"
    try:
        number = float(cast("Any", ms))
    except (TypeError, ValueError, OverflowError):
        return "\u2014"
    if number < 60000:
        return "%.1fs" % (number / 1000)
    if number < 3600000:
        return "%.1fm" % (number / 60000)
    return "%.1fh" % (number / 3600000)

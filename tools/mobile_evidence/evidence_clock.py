"""Logcat and capture clocks for the evidence join: epoch ms, year, offset, stamp."""

from __future__ import annotations

import calendar
import datetime
import json

from tools.mobile_evidence import grammar


def _epoch_ms(parts, offset_ms):
    """A logcat wall-clock stamp as epoch milliseconds, given the device's UTC offset."""
    year, mon, day, hh, mm, ss, ms = parts
    try:
        dt = datetime.datetime(year, mon, day, hh, mm, ss)
    except ValueError:  # 29 Feb in a non-leap candidate year
        return None
    return calendar.timegm(dt.timetuple()) * 1000 + ms - offset_ms


def capture_offset(source, default=0):
    """The device's UTC offset, from the structured log's own ``utcOffsetMs`` field."""
    if source is None:
        return default
    try:
        for raw in grammar._lines_of(source):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("utcOffsetMs") is not None:
                return int(rec["utcOffsetMs"])
    except (OSError, TypeError, ValueError):
        return default
    return default


def _resolve_year(parts, offset_ms, anchor):
    """The year a logcat stamp belongs to: the candidate nearest the capture's own clock."""
    if anchor is None:
        anchor = int(datetime.datetime.now().timestamp() * 1000)
    base = datetime.datetime.fromtimestamp(
        anchor / 1000.0 + offset_ms / 1000.0, tz=datetime.timezone.utc
    ).year
    best = None
    for year in (base - 1, base, base + 1):
        ms = _epoch_ms((year, *parts), offset_ms)
        if ms is None:
            continue
        if best is None or abs(ms - anchor) < abs(best - anchor):
            best = ms
    return best


def slice_window(lines, offset_ms, anchor):
    """(first ms, last ms, stamped lines) across one logcat slice."""
    lo = hi = None
    seen = 0
    for line in grammar._lines_of(lines):
        m = grammar.RE_SLICE_TS.match(line)
        if not m:
            continue
        parts = tuple(int(x) for x in m.groups())
        ms = _resolve_year(parts, offset_ms, anchor)
        if ms is None:
            continue
        seen += 1
        lo = ms if lo is None else min(lo, ms)
        hi = ms if hi is None else max(hi, ms)
    return lo, hi, seen


def _slice_value(value, offset_ms, anchor):
    """A logcat slice stamp as epoch ms, or None when it does not parse."""
    m = grammar.RE_SLICE_TS.match(value)
    if not m:
        return None
    return _resolve_year(tuple(int(x) for x in m.groups()), offset_ms, anchor)


def normalise_clock(report, offset_ms, anchor=None):
    """Make every ``clock`` value numeric: a logcat capture stores ``MM-DD HH:MM:SS.mmm``."""
    clock = report.get("clock") or {}
    for run, table in clock.items():
        for key, value in list(table.items()):
            if isinstance(value, str):
                table[key] = _slice_value(value, offset_ms, anchor)
    for r in report.get("appRuns") or []:
        for key in ("firstTs", "lastTs"):
            value = r.get(key)
            if isinstance(value, str):
                r[key] = _slice_value(value, offset_ms, anchor)
    return report


def stamp(ms, offset_ms=0):
    """A capture timestamp as the device would have shown it. Empty for a missing one."""
    if not isinstance(ms, (int, float)):
        return ""
    return datetime.datetime.fromtimestamp(
        ms / 1000.0 + offset_ms / 1000.0, tz=datetime.timezone.utc
    ).strftime("%d %b %H:%M:%S")

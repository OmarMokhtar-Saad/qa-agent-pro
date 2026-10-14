"""Splicing the logcat-only network stream into the structured events.

Each network line lands at ``anchor.seq + 0.5`` against the nearest preceding event
and inherits its turn.
"""

from __future__ import annotations

import bisect
import datetime
from typing import Any, NamedTuple

from tools.mobile_evidence.grammar_clock import (
    _clock_offset,
    _logcat_rows,
    _Splice,
)
from tools.mobile_evidence.grammar_pat import _pat


class GrammarKit(NamedTuple):
    """The profile and its compiled grammar, which travel together through the grammar."""

    profile: object
    compiled: Any


class _Timeline(NamedTuple):
    """The events that carry an event clock, in clock order, and the voted offset."""

    ordered: list
    clocks: list
    offset: int

    def anchor_for(self, when):
        """The nearest event at or before ``when`` (the first one when none is)."""
        index = bisect.bisect_right(self.clocks, when) - 1
        return self.ordered[index] if index >= 0 else self.ordered[0]


def _ordered_clock(events, offset):
    """The clock-ordered timeline of ``events``, or None when none carries a clock."""
    ordered = [e for e in events if isinstance(e.get("ts"), (int, float))]
    if not ordered:
        return None
    ordered.sort(key=lambda e: e["ts"])
    return _Timeline(ordered, [e["ts"] for e in ordered], offset)


def _timeline(events, rows, sp: _Splice):
    """The timeline to splice ``rows`` into, or None when there is nothing to anchor."""
    offset = _clock_offset(rows, events, sp)
    if offset is None:
        return None
    return _ordered_clock(events, offset)


def _network_event(anchor: dict, when, spliced) -> dict:
    """One spliced network line, placed just after ``anchor`` and inheriting its turn."""
    level, text = spliced
    return {
        "appRunId": anchor["appRunId"],
        "runIndex": anchor["runIndex"],
        "cont": False,
        "seq": (anchor["seq"] or 0) + 0.5,
        "ts": when,
        "monoNanos": None,
        "durationMs": None,
        "level": level,
        "category": None,
        "msg": text,
        "fields": {},
        "sessionId": anchor.get("sessionId"),
        "connectionId": anchor.get("connectionId"),
        "turnId": anchor.get("turnId"),
        "spanId": anchor.get("spanId"),
    }


def _splice_rows(rows, sp: _Splice, tl: _Timeline) -> list:
    """The network events of the rows whose message is a network line with a clock."""
    spliced = []
    for stamp, msg, level in rows:
        text = sp.text(msg)
        if not sp.net.match(text):
            continue
        wall = sp.wall(stamp)
        if wall is None:
            continue
        when = wall - tl.offset
        spliced.append(_network_event(tl.anchor_for(when), when, (level, text)))
    return spliced


# The clocks differ (epoch ms UTC vs a local wall clock with no year or zone), so the
# offset is VOTED: messages that occur exactly once on both sides anchor a delta,
# bucketed to the quarter hour; the modal bucket wins. Each network line lands at
# ``anchor.seq + 0.5`` against the nearest preceding event and inherits its turn.
def merge_logcat_network(events, logcat_lines, kit) -> int:
    """Splice logcat's network lines into the structured ``events``, in place; the count.

    The offset is voted from messages seen once on both sides; no anchors, no merge.
    """
    net = _pat(kit.compiled, "net")
    if net is None:
        return 0
    tag = str(getattr(kit.profile, "logcat_tag", "") or "")
    rows = _logcat_rows(logcat_lines, tag)
    if not rows:
        return 0
    sp = _Splice(datetime.datetime.now().year, kit.profile, kit.compiled, net)
    tl = _timeline(events, rows, sp)
    if tl is None:
        return 0
    spliced = _splice_rows(rows, sp, tl)
    events.extend(spliced)
    events.sort(key=lambda e: (e["runIndex"], e["seq"] is None, e["seq"] or 0))
    return len(spliced)

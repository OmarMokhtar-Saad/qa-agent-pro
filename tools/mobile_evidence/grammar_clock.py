"""The clock of a logcat slice against the structured event clock.

The clocks differ (epoch ms UTC vs a local wall clock with no year or zone), so the
offset is VOTED: messages that occur exactly once on both sides anchor a delta,
bucketed to the quarter hour; the modal bucket wins; no anchors, no offset.
"""

from __future__ import annotations

import collections
import datetime
from dataclasses import dataclass
from typing import Any

from tools.mobile_evidence.grammar_consts import logcat_line
from tools.mobile_evidence.grammar_pat import _head_re, _prefix_re
from tools.mobile_evidence.grammar_values import _lines_of


def _runlog_text(msg: str, profile, compiled) -> str:
    """A logcat message reduced to what the structured log would have stored for it."""
    msg = _head_re(profile, compiled).sub("", msg or "", count=1)
    pre = _prefix_re(profile).match(msg)
    if pre and pre.end() > 0:
        msg = msg[pre.end() :]
    return msg.strip()


def _wall_millis(stamp: str, year: int):
    """``MM-DD HH:MM:SS.mmm`` as epoch millis, read as if UTC (one half of a DIFFERENCE)."""
    try:
        month, day = stamp[:5].split("-")
        hour, minute, rest = stamp[6:].split(":")
        second, millis = rest.split(".")
        when = datetime.datetime(
            year,
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            int(millis) * 1000,
            tzinfo=datetime.timezone.utc,
        )
    except (ValueError, IndexError):
        return None
    return int(when.timestamp() * 1000)


def _logcat_rows(logcat_lines, tag: str) -> list:
    """The (ts, msg, level) of every logcat line carrying the SDK's tag."""
    rows = []
    for raw in _lines_of(logcat_lines):
        m = logcat_line(raw.rstrip("\n"))
        if m and (not tag or m.group("tag").strip() == tag):
            rows.append((m.group("ts"), m.group("msg"), m.group("level")))
    return rows


@dataclass(frozen=True)
class _Splice:
    """What reading one logcat row needs: the year, the profile, its patterns, the net regex."""

    year: int
    profile: object
    compiled: dict
    net: Any = None

    def text(self, msg: str) -> str:
        """The message as the structured log would have stored it."""
        return _runlog_text(msg, self.profile, self.compiled)

    def wall(self, stamp: str):
        """The wall clock of a logcat stamp, in epoch millis read as UTC."""
        return _wall_millis(stamp, self.year)


def _anchors(events, sp: _Splice) -> dict:
    """The event clock of every message that occurs exactly once among the stamped events."""
    seen = collections.Counter(
        sp.text(e.get("msg") or "") for e in events if e.get("ts")
    )
    stamped = {}
    for event in events:
        text = sp.text(event.get("msg") or "")
        if event.get("ts") and seen[text] == 1:
            stamped[text] = event["ts"]
    return stamped


def _vote(rows, anchors: dict, sp: _Splice) -> collections.Counter:
    """One vote per row that anchors a quarter-hour bucket of the clock difference."""
    votes: collections.Counter = collections.Counter()
    for stamp, msg, _level in rows:
        anchor = anchors.get(sp.text(msg))
        wall = sp.wall(stamp)
        if anchor is not None and wall is not None and isinstance(anchor, (int, float)):
            votes[round((wall - anchor) / 900000.0)] += 1
    return votes


def _clock_offset(rows, events, sp: _Splice):
    """The modal quarter-hour offset between logcat's wall clock and the event clock.

    Messages that occur exactly once on both sides anchor a delta; ``None`` when no
    message anchors one.
    """
    votes = _vote(rows, _anchors(events, sp), sp)
    if not votes:
        return None
    return votes.most_common(1)[0][0] * 900000

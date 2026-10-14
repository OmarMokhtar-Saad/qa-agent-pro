"""Disk-sourced values made safe to interpolate into the mobile lane's markdown.

Leaf of the ``render_*`` split of ``render.py``.
"""

from __future__ import annotations

from typing import Any

from config.settings_guard import never_raise

# Every value below comes off DISK, and session_runs.py builds and run_store writes model-supplied
# goal/package/serial into that manifest. A run resumes in any chat from its id,
# so text planted by one chat is read by another chat's model: this is a
# cross-chat injection channel, not merely display text.
_MD_BREAKOUT = str.maketrans({"`": "'", "\n": " ", "\r": " ", "\t": " "})
_MAX_FIELD_CHARS = 120


def _field(value: object, fallback: str = "") -> str:
    """A disk-sourced value, safe to interpolate into markdown.

    Backticks become apostrophes so a value cannot close the code span it is
    rendered in -- a planted fence did exactly that -- newlines collapse so it
    cannot begin a block, and the whole thing is capped. Never raises.
    """
    with never_raise():
        # translate() already maps newline, return and tab to a space, so the
        # only work left here is the BOUND: these values are model-supplied and
        # replayed from disk, and an unbounded one pushes the rest of the
        # report off a tester's screen.
        text = str(value if value is not None else "").translate(_MD_BREAKOUT)
        return text.strip()[:_MAX_FIELD_CHARS] or fallback
    return fallback


def _count(value: Any) -> int:
    """A disk-sourced counter as a non-negative int. Never raises."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0

"""Replay waits: did the last action already change the screen a change-wait starts on?

A ``wait_until_changed`` after a tap waits for a change AWAY from the screen it
starts on. When the tap's own settle already moved the screen, that change has
happened and the wait would run to its full bound for nothing (S3).

The hash lives in ``ctx.env`` (per replay, so nothing leaks between runs).
"""

from __future__ import annotations

import re

from tools.mobile import a11y_events
from tools.mobile.replay.context import Context

_KEY = "before_action_hash"
_GONE = "gone_satisfied"
_CALM = "calm_hash"


def note_action(ctx: Context, before_hash: str) -> None:
    """Remember the content hash of the screen an action started on."""
    ctx.env[_KEY] = str(before_hash or "")
    ctx.env.pop(_GONE, None)
    ctx.env.pop(_CALM, None)  # an actuation ends any proof the screen was calm


def _clock(ctx: Context) -> int | None:
    """The a11y service's event count for this device now (``None``: no clock)."""
    got = a11y_events.latest(str(getattr(ctx, "serial", "") or ""))
    return None if got is None else got.count


def note_calm(ctx: Context, shown: str) -> None:
    """The screen with content hash *shown* was proven settled (idle, gone, anchor
    or an absent optional tap). Valid until the next actuation, and only while the
    a11y event count has not moved since (a late popup is an event)."""
    ctx.env[_CALM] = (str(shown or ""), _clock(ctx))


def calm_for(ctx: Context, shown: str) -> bool:
    """True when *shown* is the screen last proven settled, nothing was actuated and
    no a11y event was heard since the proof was taken."""
    held = ctx.env.get(_CALM)
    if not shown or not held or held[0] != shown:
        return False
    return held[1] is None or held[1] == _clock(ctx)


def note_gone(ctx: Context) -> None:
    """A wait for something to go away was met: that IS the change."""
    ctx.env[_GONE] = True


def clear_action(ctx: Context) -> None:
    """Forget the remembered hash: only the wait right after a device op may use it."""
    env = getattr(ctx, "env", None) or {}
    env.pop(_KEY, None)
    env.pop(_GONE, None)


_LOADING = re.compile(r"progress|spinner|loading|lottie|shimmer|skeleton", re.I)


def _loading_row(row: object) -> bool:
    """A node whose class, role or description names a generic loading indicator."""
    if not isinstance(row, dict):
        return False
    return any(_LOADING.search(str(row.get(k) or "")) for k in ("cls", "role", "desc"))


def _spinning(screen: object) -> bool:
    """A screen showing a loading indicator: a transient state, not the change we
    wait for. Matches generic Android loading names (``ProgressBar`` subclasses,
    spinner/Lottie/shimmer views, a ``progress``/``loading`` role or description)."""
    rows = screen.get("elements") if isinstance(screen, dict) else None
    return any(_loading_row(r) for r in rows or [])


def already_changed(ctx: Context, now_hash: str, screen: object = None) -> bool:
    """True once, when the last action started on a different, settled screen."""
    env = getattr(ctx, "env", None) or {}
    if env.pop(_GONE, False):
        return True
    before = env.pop(_KEY, "")
    if _spinning(screen):
        return False
    return bool(before and now_hash and before != now_hash)

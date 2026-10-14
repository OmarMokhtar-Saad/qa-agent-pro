"""Replay waits: did the last action already change the screen a change-wait starts on?

A ``wait_until_changed`` after a tap waits for a change AWAY from the screen it
starts on. When the tap's own settle already moved the screen, that change has
happened and the wait would run to its full bound for nothing (S3).

The hash lives in ``ctx.env`` (per replay, so nothing leaks between runs).
"""

from __future__ import annotations

from tools.mobile.replay.context import Context

_KEY = "before_action_hash"


def note_action(ctx: Context, before_hash: str) -> None:
    """Remember the content hash of the screen an action started on."""
    ctx.env[_KEY] = str(before_hash or "")


def clear_action(ctx: Context) -> None:
    """Forget the remembered hash: only the wait right after a device op may use it."""
    (getattr(ctx, "env", None) or {}).pop(_KEY, None)


def _spinning(screen: object) -> bool:
    """A screen showing a progress bar: a transient state, not the change we wait for."""
    rows = screen.get("elements") if isinstance(screen, dict) else None
    return any(
        "ProgressBar" in str(r.get("cls")) for r in rows or [] if isinstance(r, dict)
    )


def already_changed(ctx: Context, now_hash: str, screen: object = None) -> bool:
    """True once, when the last action started on a different, settled screen."""
    before = (getattr(ctx, "env", None) or {}).pop(_KEY, "")
    if _spinning(screen):
        return False
    return bool(before and now_hash and before != now_hash)

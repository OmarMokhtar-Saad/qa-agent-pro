"""Replay waits: a text wait that timed out re-checks once off the accessibility tree.

Measured (``mrun-20261021-162641-0fedce``): a button label stayed ``Download`` in
the accessibility tree while the UI showed ``Downloading...``; the wait timed out
having read only that tree. One fresh non-accessibility uiautomator read settles
it, and the miss sentence names every source that was read.
"""

from __future__ import annotations

from tools.mobile import a11y_confirm, dump_latency
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import FRESH_READ_BUDGET_S
from tools.mobile.replay.dump_screen import _observed

_KEY = "wait_source"
_TREE = "the accessibility tree"
_FRESH = " and a fresh uiautomator read"


def recheck_reserve_ms(ms: int, serial: str) -> int:
    """Budget ms kept for the final fresh read: p90 x1.5 (1 s..3 s), at most half of *ms*."""
    want = max(1000, int((dump_latency.p90_ms(serial) or 0) * 1.5))
    return max(0, min(want, int(FRESH_READ_BUDGET_S * 1000), ms // 2))


async def fresh_screen(ctx: Context, cap_s: float = FRESH_READ_BUDGET_S) -> object:
    """A fresh non-accessibility read (cap *cap_s*), pruned like a poll, or ``None``."""
    raw = await a11y_confirm.uiautomator_aside(ctx.serial, "text_recheck", cap_s)
    if raw.get("error"):
        return None
    seen = await _observed(ctx, raw)
    return None if seen.get("error") else seen.get("content")


def note_source(ctx: Context, rechecked: bool) -> None:
    """Remember which sources the miss was read from."""
    env = getattr(ctx, "env", None)
    if env is not None:
        env[_KEY] = _TREE + (_FRESH if rechecked else "")


def with_source(ctx: Context, missed: str) -> str:
    """*missed* plus the sources of the last text-wait miss (``""`` when none)."""
    named = (getattr(ctx, "env", None) or {}).pop(_KEY, "")
    return f"{missed} (checked {named})" if named else missed

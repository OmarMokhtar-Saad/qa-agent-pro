"""Replay waits: refuse a condition wait the recent screens prove cannot end."""

from __future__ import annotations

from tools.mobile import wait_guard
from tools.mobile.replay.context import Context
from tools.mobile.replay.screen_seen import reads
from tools.mobile.replay.waits_prior import is_spinning
from tools.mobile.replay.waits_types import CondStep, WaitSpec

OUTCOME = "wait_refused"


def _op_target(action: object, spec: WaitSpec) -> tuple:
    """``(guard op, target)`` for a gone or idle condition, else ``("", "")``."""
    kind, text = spec[0], spec[1]
    if kind == "idle":
        return "wait_until_idle", ""
    if kind == "gone" or (kind == "element" and getattr(action, "until_gone", False)):
        rid_text = str(getattr(action, "until_rid_text", "") or "")
        return "wait_until_gone", str(text or rid_text or "")
    return "", ""


def refusal(ctx: Context, step: CondStep, screen: object) -> str:
    """Why this wait can never end ("" when it may); a loading screen is never refused."""
    op, target = _op_target(step[0], step[1])
    if not op or (op == "wait_until_gone" and is_spinning(screen)):
        return ""
    return wait_guard.impossible_wait(op, target, reads(ctx.serial))


def stamp_refused(entry: dict, reason: str) -> None:
    """Close the entry like a missed wait, with the guard's reason as the detail."""
    entry["outcome"], entry["detail"] = OUTCOME, "wait refused: " + reason

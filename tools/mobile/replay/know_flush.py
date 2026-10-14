"""Replay knowledge: flushing the buffered tallies and plug events."""

from __future__ import annotations

import logging

from tools.mobile import app_knowledge, knowledge_hooks
from tools.mobile.replay.context import Context

logger = logging.getLogger("tools.mobile.executor")


def _flush_kbuf(ctx: Context) -> None:
    """Hand ``ctx.kbuf`` to the plugs' ``flush`` in ONE batched call, then empty
    it. Never raises."""
    events = list(getattr(ctx, "kbuf", None) or [])
    if not events:
        return
    try:
        knowledge_hooks.flush_kbuf(ctx, events)
    except Exception:
        logger.exception("mobile app knowledge: kbuf flush failed")
    finally:
        ctx.kbuf = []


def _write_counters(ctx: Context, know: dict) -> None:
    """Write the buffered counters and events, then reset them. Never raises."""
    try:
        if know.get("counts") or know.get("events"):
            tallies = app_knowledge.Tallies(
                know.get("counts") or {},
                know.get("events") or [],
                str(getattr(ctx, "run_id", "") or ""),
            )
            app_knowledge.flush_counters(str(know.get("package") or ""), tallies)
    except Exception:
        logger.exception("mobile app knowledge: flush failed")
    finally:
        know["counts"] = {}
        know["events"] = []
        know["spent_ms"] = 0


def _flush_knowledge(ctx: Context) -> None:
    """Write the tallies buffered by ``_apply_knowledge``, in ``replay``'s
    ``finally`` so success, error, exception and abort all flush. Never raises.

    It then DROPS the loaded notes (``ctx.knowledge = None``), so the next
    ``replay`` on the same Context reloads them: a note added or retired
    between two replays is seen by the second one."""
    know = getattr(ctx, "knowledge", None)
    ctx.knowledge = None
    _flush_kbuf(ctx)
    if not isinstance(know, dict):
        return
    know.pop("snap", None)
    _write_counters(ctx, know)

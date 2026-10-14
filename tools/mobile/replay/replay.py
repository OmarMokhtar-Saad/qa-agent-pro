"""Replay engine: the replay entry point; the loop lives in the ``loop_*`` leaves. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tools.mobile import knowledge_run, knowledge_runner
from tools.mobile.replay.context import Context
from tools.mobile.replay.knowledge import _flush_knowledge, _read_knowledge
from tools.mobile.replay.loop_keyboard import _replay_keyboard
from tools.mobile.replay.loop_open import _replay_first_screen  # noqa: F401
from tools.mobile.replay.loop_settle import _settle_device  # noqa: F401

logger = logging.getLogger("tools.mobile.executor")


async def _begin_knowledge_run(ctx: Context) -> Any:
    """Run facts for this replay (version, env), cached per run; ``None`` on any
    failure. Copies the version and env onto the Context."""
    if not getattr(ctx, "run_id", None):
        return None
    try:
        facts = await knowledge_run.begin_run(ctx, knowledge_runner.resume_pending)
        ctx.run_facts = facts
        ctx.app_version = facts.app_version
        ctx.env = dict(facts.env)
        return facts
    except Exception:
        logger.exception("mobile app knowledge: begin_run failed")
        return None


async def replay(script: Any, ctx: Context) -> dict:
    """Execute *script*; the contract is :func:`_replay_keyboard`'s.

    Loads this app's saved notes ONCE (read-only) and flushes their buffered
    counters in a ``finally``, so success, error, exception and abort all
    flush. Both steps degrade to "no notes" and never raise, and both run in a
    worker thread so a busy SQLite file (up to its timeout) never blocks the loop.
    """
    try:
        # Inside the try: a cancel during the load still reaches the flush,
        # which drops ctx.knowledge. The worker only READS; the assignment
        # happens here, on the loop, so a cancelled load never assigns.
        if getattr(ctx, "knowledge", None) is None:
            package = str(getattr(ctx, "package", "") or "")
            facts = await _begin_knowledge_run(ctx)
            args = (package,) if facts is None else (package, facts)
            ctx.knowledge = await asyncio.to_thread(_read_knowledge, *args)
        return await _replay_keyboard(script, ctx)
    finally:
        await asyncio.to_thread(_flush_knowledge, ctx)

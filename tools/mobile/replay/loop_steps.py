"""Replay engine: the loop over a script's steps. Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

import logging
import time
from typing import Any

from tools.mobile import run_redact
from tools.mobile.replay.context import Context, _ReplayRun, _ReplayState, _StepCursor
from tools.mobile.replay.loop_end import _end_of_script
from tools.mobile.replay.loop_one import Turn, _replay_one
from tools.mobile.replay.loop_open import Start, _replay_open
from tools.mobile.replay.steps_notrun import with_not_run
from tools.mobile.replay.verdicts import _budget_seconds

logger = logging.getLogger("tools.mobile.executor")


# Execute *script* against ``ctx``. Never raises.
#
# ``{"error": None, "content": {"status", "trace", "screen", "verdict",
# "reason", "stopped_at"}}``. Top-level ``error`` is reserved for this
# function itself failing -- a device refusal is ``status == "error"`` with a
# trace, because the trace is the only thing that tells a tester how far the
# case got.
async def _replay_steps(script: Any, ctx: Context) -> dict:
    """Execute *script* against ``ctx``; the reply is redacted like every run file."""
    return run_redact.redact(await _replay_steps_raw(script, ctx))


async def _replay_steps_raw(script: Any, ctx: Context) -> dict:
    """Execute *script* against ``ctx``. Never raises (the reply: above)."""
    trace: list[dict] = []
    screen = ctx.screen if isinstance(ctx.screen, dict) else None
    try:
        items = list(getattr(script, "actions", None) or [])
        screen, reply = await _replay_open(ctx, script, Start(screen, trace))
        if reply is not None:
            return reply
        run = _ReplayRun(ctx, items, trace)
        state = _ReplayState(screen, time.monotonic() + _budget_seconds(ctx))
        for index, action in (cursor := _StepCursor(items)):
            reply = await _replay_one(run, Turn(cursor, state), (index, action))
            if reply is not None:
                return with_not_run(run, reply, index)

        return _end_of_script(run, state.screen)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.executor.replay failed")
        return {"error": str(exc), "content": None}

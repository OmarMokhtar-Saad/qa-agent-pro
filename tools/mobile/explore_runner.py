"""The exploratory lane: goal, a turn ladder, a deadline, and one extension.

Four independent stops, because an exploratory session with only one is a
session that runs until somebody notices: ``max_turns``, a wall-clock deadline,
the model declaring ``goal_reached``, and the destructive guard inside the
executor. Exactly ONE extension may be requested, and only with a stated reason
-- "the model asked again" is not a reason, and an unbounded extension is the
same defect as no deadline.

Every clock reading is injected (``now=``). Asserting a 20-minute deadline by
sleeping would be slower than the thing measured and would sit below its own
timing noise floor; the tests drive stated timestamps and assert the STATE
TRANSITION instead.

A facade: the lane lives in the ``explore_runner_*`` leaves and every name below is
re-exported by identity. ``next_turn`` and ``_observe_screen`` stay defined here,
because source scanners read this file for the screen-store and density calls.
"""

from __future__ import annotations

import logging

from tools.mobile import adb, run_store
from tools.mobile.explore_runner_apply import apply_turn_result  # noqa: F401
from tools.mobile.explore_runner_consts import (  # noqa: F401
    _PACKAGE_RE,
    EXTENSION_S,
    EXTENSION_TURNS,
    GUARD_REFUSED_NOTICE,
    HANDOVER_REFUSED_PREFIXES,
    HANDOVER_REFUSED_WORDS,
    MAX_EXTENSIONS,
    MAX_PACKAGE_CHARS,
    MAX_SUB_GOAL_CHARS,
    MAX_WATCH_ITEMS,
    RUNNING,
)
from tools.mobile.explore_runner_handover import handover  # noqa: F401
from tools.mobile.explore_runner_notices import (  # noqa: F401
    EXTENSION_NOT_GRANTED,
    EXTENSION_NOT_GRANTED_CONTINUE,
    EXTENSION_REFUSAL,
    EXTENSION_SPENT,
    EXTENSION_SPENT_CONTINUE,
    NO_FINDING_NOTICE,
)
from tools.mobile.explore_runner_state import new_state, remaining  # noqa: F401
from tools.mobile.explore_runner_turn import halt_result, packet_turn
from tools.mobile.explore_runner_types import Held, StateSpec, TurnEnv  # noqa: F401
from tools.mobile.explore_stop import (
    DEADLINE_S,  # noqa: F401
    MAX_GOAL_CHARS,  # noqa: F401
    MAX_TURNS,  # noqa: F401
    STOP_DEADLINE,  # noqa: F401
    STOP_FINDING,  # noqa: F401
    STOP_GOAL,  # noqa: F401
    STOP_GUARD_REFUSED,  # noqa: F401
    STOP_TURNS,  # noqa: F401
    _now,  # noqa: F401
    display_stop,  # noqa: F401
    stop_reason,  # noqa: F401
)  # identity re-exports (P3 shim rule); unused ones carry noqa
from tools.mobile.providers import composite
from tools.mobile.replay.dump import dump_raw, resolve_activity

logger = logging.getLogger(__name__)


async def _observe_screen(ctx: object) -> dict:
    """Dump and prune the foreground screen: the pruned result, or an error dict."""
    dumped = await dump_raw(getattr(ctx, "serial", ""))
    if dumped.get("error"):
        return dumped
    sized = await adb.display_size(getattr(ctx, "serial", ""))
    # Same device fact, same cache, same reason as the case lane's.
    dpi = await adb.display_density(getattr(ctx, "serial", ""))
    return composite.observe(
        dumped.get("content"),
        # ONE producer, shared with the replay: see resolve_activity.
        await resolve_activity(ctx),
        composite.ScreenView(sized.get("content"), dpi.get("content")),
    )


# Same contract as before the split: dump, prune and build the turn packet -- or stop.
#
# ``{"error", "content": {"status", "state", "screen", "packet", "remaining"}}``
# with ``status`` one of ``running`` / the three stop reasons.
async def next_turn(run_id: str, state: object, env: TurnEnv) -> dict:
    """Dump, prune and build the turn packet -- or stop."""
    try:
        body = dict(state if isinstance(state, dict) else {})
        halted = halt_result(body, env.now)
        if halted is not None:
            return halted

        pruned = await _observe_screen(env.ctx)
        if pruned.get("error"):
            return pruned
        screen = pruned.get("content") or {}

        # Same contract as case_runner's: a screen the report can draw,
        # stored best-effort, never able to stop a turn.
        run_store.write_screen(run_id, screen)
        return packet_turn(run_id, Held(body, screen), env.now)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.explore_runner.next_turn failed")
        return {"error": str(exc), "content": None}

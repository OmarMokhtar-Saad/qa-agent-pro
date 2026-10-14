"""Mobile session: the explore lane's next packet. Cut verbatim from tools/mobile/session_packets.py (C5 w17)."""

from __future__ import annotations

import logging
import time

from tools.mobile import explore_runner
from tools.mobile.session_core import STATE_REPORT, STATE_RUNNING, _busy, _in_reserve
from tools.mobile.session_explore import (
    _explore_abort_network,
    _explore_begin_network,
    _persist_explore,
)
from tools.mobile.spkt_final import _final_packet
from tools.mobile.spkt_types import PacketEnv

logger = logging.getLogger("tools.mobile.session")


# WHEN THIS TURN BEGAN, in the same unit the suite lane uses for
# a case. The checkpoint is written when the turn ENDS, so
# without this the only instant it knows is the end and its
# evidence window collapses to the pad around it.
def _start_turn(content: dict, running: bool) -> None:
    if running:
        began = dict(content.get("state") or {})
        began["turn_started"] = time.time()
        content["state"] = began


# The explore lane's reply once a turn has run.
def _explore_reply(body: dict, content: dict, running: bool) -> dict:
    return {
        "error": None,
        "content": {
            "state": STATE_RUNNING if running else STATE_REPORT,
            "packet": content.get("packet"),
            "tc_id": "",
            "status": str(content.get("status") or ""),
            "resolved": body,
        },
    }


async def _reserved_reply(body: dict, env: PacketEnv) -> dict:
    # Checked BEFORE the turn, never during: a turn dumps the screen
    # and writes state, and abandoning one half-done is worse than
    # answering with a pointer. The reserved tail is spent on ONE
    # best-effort capture instead of returning bare -- see
    # _final_packet.
    # Always attempt the capture: _final_packet self-guards with
    # asyncio.wait_for(..., timeout=max(0.0, remaining())), so an
    # already-expired budget degrades to None on its own instead
    # of skipping the attempt outright.
    return _busy(body, "", packet=await _final_packet(env.ctx, env.budget))


# The explore lane's next packet (one turn), or its busy/report answer.
async def _next_explore_packet(run_id: str, body: dict, env: PacketEnv) -> dict:
    state = body.get("explore") or {}
    if env.budget is not None and _in_reserve(env.budget):
        return await _reserved_reply(body, env)
    turn = await explore_runner.next_turn(
        run_id, state, explore_runner.TurnEnv(env.ctx)
    )
    if turn.get("error"):
        return turn
    content = turn.get("content") or {}
    running = str(content.get("status") or "") == explore_runner.RUNNING
    _start_turn(content, running)
    content["state"] = await _explore_turn_network(run_id, content, env)
    _persist_explore(run_id, content.get("state"))
    return _explore_reply(body, content, running)


# The turn's state after starting (or, once stopped, aborting) its capture.
#
# The wire, for this turn. Started HERE, at the packet build, for
# the same reason the suite lane starts it before its launch: the
# capture has to cover what the turn is about to do. A stopped
# session, a physical device or a flag that is off all land as a
# record which SAYS so, and none of them can block a turn.
# The budget is re-read HERE and not only before the turn: the
# capture adds two device round trips AFTER the only earlier check,
# and on a slow emulator that is what pushes the packet build past
# the client's timeout. Losing the capture costs a section that
# says why; losing the call costs the turn.
async def _explore_turn_network(run_id: str, content: dict, env: PacketEnv) -> object:
    running = str(content.get("status") or "") == explore_runner.RUNNING
    if running and not (env.budget is not None and env.budget.expired()):
        return await _explore_begin_network(run_id, content.get("state"), env.ctx)
    if not running:
        # The session has STOPPED. Any capture a previous packet build
        # started is now never going to be read, so it is stopped and
        # its file deleted here rather than left recording the tester's
        # device until something else displaces it.
        return await _explore_abort_network(run_id, content.get("state"), env.ctx)
    return content.get("state")

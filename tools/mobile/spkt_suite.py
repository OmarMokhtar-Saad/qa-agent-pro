"""Mobile session: the suite lane's next packet. Cut verbatim from tools/mobile/session_packets.py (C5 w17)."""

from __future__ import annotations

import logging

from tools.mobile import case_runner
from tools.mobile.replay.context import Context
from tools.mobile.session_core import (
    STATE_GATE,
    STATE_REPORT,
    STATE_RUNNING,
    _busy,
    _in_reserve,
)
from tools.mobile.spkt_evidence import _finish_evidence
from tools.mobile.spkt_final import _final_packet as final_packet
from tools.mobile.spkt_types import PacketEnv, Pick
from tools.mobile.sruns_cases import load_case

logger = logging.getLogger("tools.mobile.session")


# The suite lane's next-packet reply shape.
def _suite_reply(state: str, body: dict, pick: Pick) -> dict:
    return {
        "error": None,
        "content": {
            "state": state,
            "packet": pick.packet,
            "tc_id": pick.tc_id,
            "resolved": body,
        },
    }


async def _start_suite_case(run_id: str, loaded: dict, ctx: Context) -> dict:
    return await case_runner.start_case(run_id, loaded["content"], ctx)


async def _reserved_reply(body: dict, tc_id: str, env: PacketEnv) -> dict:
    # start_case force-stops the app, relaunches it and dumps the screen.
    # With the budget gone that work would land after the client has
    # already given up on the call, so nothing is started at all. The
    # reserved tail is spent on ONE best-effort capture instead -- see
    # final_packet.
    # Always attempt the capture -- see the matching comment in the
    # explore-lane busy branch (spkt_explore).
    return _busy(body, tc_id, packet=await final_packet(env.ctx, env.budget))


def _started_reply(body: dict, started: dict) -> dict:
    content = started.get("content") or {}
    pick = Pick(content.get("packet"), str(content.get("tc_id") or ""))
    return _suite_reply(STATE_RUNNING, body, pick)


# The suite lane's next packet: report, gate, busy or a started case.
async def _next_suite_packet(run_id: str, body: dict, env: PacketEnv) -> dict:
    tc_id = str(body.get("next_tc_id") or "")
    if body.get("finished"):
        await _finish_evidence(run_id, body)
        return _suite_reply(STATE_REPORT, body, Pick(None, ""))
    if body.get("gate") and not env.past_gate:
        return _suite_reply(STATE_GATE, body, Pick(None, tc_id))
    loaded = load_case(run_id, tc_id)
    if loaded.get("error"):
        return loaded
    if env.budget is not None and _in_reserve(env.budget):
        return await _reserved_reply(body, tc_id, env)
    started = await _start_suite_case(run_id, loaded, env.ctx)
    return started if started.get("error") else _started_reply(body, started)

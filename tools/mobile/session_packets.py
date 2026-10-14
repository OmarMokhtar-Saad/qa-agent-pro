"""Mobile session: next-packet and the final-packet capture. Cut verbatim from tools/mobile/session.py (P4.4).

The lane builders, the final capture and the evidence pull live in the
``spkt_*`` leaves; this module keeps ``next_packet`` and the names every
importer and test patches (``resolve`` and ``context_for`` are read here).
"""

from __future__ import annotations

import asyncio  # noqa: F401
import logging
import time  # noqa: F401

from tools.mobile import adb, case_runner, explore_runner, run_store  # noqa: F401
from tools.mobile.providers import composite  # noqa: F401
from tools.mobile.replay.context import Context  # noqa: F401
from tools.mobile.replay.dump import dump_raw  # noqa: F401
from tools.mobile.session_core import (  # noqa: F401
    LANE_EXPLORE,
    STATE_GATE,
    STATE_REPORT,
    STATE_RUNNING,
    Budget,
    _busy,
    _in_reserve,
)
from tools.mobile.session_explore import (  # noqa: F401
    _explore_abort_network,
    _explore_begin_network,
    _persist_explore,
)
from tools.mobile.session_runs import context_for, load_case, resolve  # noqa: F401
from tools.mobile.spkt_evidence import _finish_evidence, finish_evidence  # noqa: F401
from tools.mobile.spkt_explore import (  # noqa: F401
    _explore_turn_network,
    _next_explore_packet,
)
from tools.mobile.spkt_final import _final_packet  # noqa: F401
from tools.mobile.spkt_suite import _next_suite_packet, _suite_reply  # noqa: F401
from tools.mobile.spkt_types import PacketEnv, PacketOpts, Pick  # noqa: F401
from tools.mobile_evidence import capture  # noqa: F401

logger = logging.getLogger("tools.mobile.session")


# The next thing the tester's model must answer, or a terminal state.
#
# ``{"error", "content": {"state", "packet", "tc_id", "resolved", ...}}``.
#
# ``opts.past_gate`` is the tester having ALREADY answered the soft "keep going?"
# gate. It belongs here, at the one place the gate state is decided, because
# the gate was previously evaluated twice -- once in the handler, which
# honoured the override, and again in the renderer, which did not -- so a run
# past the gate looped on it forever. One decision, one answer.
async def next_packet(
    run_id: str, session_token: str = "", *, opts: PacketOpts = PacketOpts()
) -> dict:
    try:
        resolved = resolve(run_id, session_token)
        if resolved.get("error"):
            return resolved
        body = resolved["content"] or {}
        env = PacketEnv(context_for(body), opts.budget, opts.past_gate)
        if str(body.get("lane")) == LANE_EXPLORE:
            return await _next_explore_packet(run_id, body, env)
        return await _next_suite_packet(run_id, body, env)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.next_packet failed")
        return {"error": str(exc), "content": None}

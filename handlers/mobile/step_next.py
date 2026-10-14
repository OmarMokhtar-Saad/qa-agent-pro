"""Next packet of the mobile step handlers: the packet, the gate or the summary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from handlers import _common
from handlers.mobile.step_packet import (
    PacketAsk,
    _mobile_packet_header,
    _mobile_packet_text,
)
from handlers.mobile.step_report import _mobile_report_text
from tools.mobile import render as mobile_render
from tools.mobile import session_core, session_packets
from tools.mobile.session_core import STATE_BUSY, STATE_GATE, STATE_REPORT
from tools.mobile.session_packets import PacketOpts


@dataclass(frozen=True)
class NextTurn:
    """Who asks for the next packet: run, chat token, budget, gate, first packet."""

    run_id: str
    token: str
    budget: Any
    past_gate: bool
    first_packet: bool


@dataclass(frozen=True)
class NextOpts:
    """The optional parts of a next-packet request."""

    progress: _common.ProgressCb = None
    past_gate: bool = False
    budget: Any = None
    first_packet: bool = False


def _gate_counts(body: dict) -> dict:
    """The done/total/failed tally the gate block shows."""
    resolved = body.get("resolved") or {}
    return {
        "done": resolved.get("done"),
        "total": resolved.get("total"),
        "failed": resolved.get("failed"),
    }


async def _next_packet_text(turn: NextTurn, body: dict, bounded: Any) -> str:
    """The step heading plus the packet, with this turn's screen captured."""
    return _mobile_packet_header(turn.run_id, body, turn.first_packet) + (
        await _mobile_packet_text(
            PacketAsk(
                body.get("packet"),
                turn.token,
                resolved=body.get("resolved"),
                budget=bounded,
            )
        )
    )


async def _next_body(turn: NextTurn, body: dict, bounded: Any) -> str:
    """The reply for one state of ``next_packet``: busy, report, gate or packet."""

    state = str(body.get("state") or "")
    if state == STATE_BUSY:
        return mobile_render.busy_block(
            turn.run_id, str(body.get("tc_id") or ""), packet=body.get("packet")
        )
    if state == STATE_REPORT:
        return await _mobile_report_text(turn.run_id, turn.token, body)
    if state == STATE_GATE:
        return mobile_render.gate_block(_gate_counts(body))
    return await _next_packet_text(turn, body, bounded)


async def _next_reply(turn: NextTurn) -> str:
    """Ask for the next packet and render the answer."""

    bounded = turn.budget if turn.budget is not None else session_core.new_budget()
    result = await session_packets.next_packet(
        turn.run_id, turn.token, opts=PacketOpts(turn.past_gate, bounded)
    )
    if result.get("error"):
        return "⚠️ " + str(result["error"])[:400]
    return await _next_body(turn, result.get("content") or {}, bounded)


async def _mobile_next(
    run_id: str, session_token: str, opts: NextOpts | None = None
) -> str:
    """Render the next packet, the gate, or the summary. One place, three states."""
    opts = opts or NextOpts()
    turn = NextTurn(
        run_id, session_token, opts.budget, opts.past_gate, opts.first_packet
    )
    await _common.emit(opts.progress, "\U0001f4f1 Preparing the next step…")
    return await _next_reply(turn)

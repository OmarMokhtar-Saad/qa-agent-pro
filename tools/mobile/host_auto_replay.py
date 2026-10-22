"""Auto-replay in the host lane: a saved flow runs when the app stands on its first screen.

The explore lane replays a learned flow on its own (``flow_replay``); the host lane
used to leave every flow to the model. This module runs the SAME replay at the two
places the host lane builds a packet: the first packet of a case, and the packet
after a stopped step (``needs_model``). Only a flow whose FIRST saved step fits the
live screen runs, only when every input it needs is already known (secrets come from
the tester, never from the flow), never after ``flow=none``, and never when
``QA_AUTO_FLOW_REPLAY`` is 0/false/off/no. The replay stops at the first divergence;
the packet then carries the live screen plus a note saying where it stopped.
Never raises: a failure here means the model gets the plain packet.
"""

from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from types import SimpleNamespace
from typing import Any

from tools.mobile import (
    explore_runner,
    flow_replay,
    flow_replay_state,
    host_flow_goal,
    host_replay_ready,
    run_store,
)
from tools.mobile.case_run_consts import NEEDS_MODEL
from tools.mobile.case_run_types import _Ref, _Step
from tools.mobile.session_core_state import PACKET_RESERVE_S

logger = logging.getLogger(__name__)

#: Seconds one pass may spend replaying (the call budget is 45 s).
MAX_SECONDS = 30.0
#: Below this much call budget the replay is skipped, the packet matters more.
MIN_BUDGET_S = 15.0
_KEEP = 64
_NOT_RAN = SimpleNamespace(ran=False, screen=None)  # a pass with no time left
#: The ``source`` a replayed step carries in the case trace.
SOURCE = "auto_replay"
#: ``(run_id, tc_id)`` -> the replay state of that case (flows already tried).
_BODIES: OrderedDict = OrderedDict()


def enabled() -> bool:
    """False when ``QA_AUTO_FLOW_REPLAY`` is 0, false, off or no."""
    off = ("0", "false", "off", "no")
    return os.environ.get("QA_AUTO_FLOW_REPLAY", "").strip().lower() not in off


def _body(ref: _Ref, fresh: bool) -> dict:
    """The case's replay state; a case start begins with an empty one."""
    key = (ref.run_id, ref.tc_id)
    if fresh or key not in _BODIES:
        goal = host_flow_goal.title(ref.run_id, ref.tc_id)
        _BODIES[key] = {"case_id": ref.tc_id, "goal": goal}
    _BODIES.move_to_end(key)
    while len(_BODIES) > _KEEP:
        _BODIES.popitem(last=False)
    return _BODIES[key]


def _seconds(ctx: Any) -> float:
    """This pass's replay time from what the CALL has left, or 0 when it has no room."""
    deadline = getattr(ctx, "call_deadline", None)
    if deadline is None:
        return MAX_SECONDS
    left = float(deadline) - time.monotonic()
    return 0.0 if left < MIN_BUDGET_S else min(MAX_SECONDS, left - PACKET_RESERVE_S)


async def _walk(ref: _Ref, screen: dict, body: dict) -> Any:
    """The replay pass; the call's context holds THIS pass's budget while it runs."""
    seconds = _seconds(ref.ctx)
    if not seconds:  # crossed the floor since run() checked: 0.0 would read as "no cap"
        return _NOT_RAN
    before = getattr(ref.ctx, "budget_s", None)
    ref.ctx.budget_s = seconds  # the in-flow batch deadline reads it
    try:
        auto = flow_replay.Auto(screen, body, False, True, seconds)
        return await flow_replay.run(ref.run_id, ref.ctx, auto)
    finally:
        ref.ctx.budget_s = before


async def _read_again(ref: _Ref, last: Any) -> Any:
    """The screen read from the device; the last known one when that fails."""
    try:
        again = await explore_runner.observe_screen(ref.ctx)
    except Exception:  # steps ran: keep the screen they ended on
        logger.exception("mobile.host_auto_replay.observe failed")
        return last
    return last if again.get("error") else again.get("content") or last


async def _replay(ref: _Ref, screen: dict, body: dict) -> tuple:
    """``(screen, note)`` after the replay, with the screen read again from the device."""
    auto = await _walk(ref, screen, body)
    if not auto.ran:
        return screen, ""
    seen = await _read_again(ref, auto.screen)
    run_store.write_screen(ref.run_id, seen)
    note = (body.get("auto_replay") or {}).get("note")
    return seen, str(note or "a saved flow was auto-replayed")


def _declined(ref: _Ref, body: dict) -> bool:
    """``flow=none`` was said, or the user's own words ask for no saved flow."""
    if flow_replay_state.opted_out(ref.run_id):
        return True
    return host_flow_goal.declined(ref.run_id, str(body.get("goal") or ""))


async def _fit_then_replay(ref: _Ref, screen: dict, body: dict) -> tuple:
    """Replay the fitting flow; with inputs owed, name them in the packet note (B15)."""
    template = host_replay_ready.fitting(ref, screen, body)
    if not template:  # a flow kept out as foreign is still named (never silent)
        listed = host_replay_ready.others(ref, screen, body)
        more = host_flow_goal.unnamed(body, listed)
        return screen, host_replay_ready.choose_note(more) if more else ""
    owed = host_replay_ready.missing(ref, template)
    more = host_replay_ready.others(ref, screen, body)
    if owed:
        seen, note = screen, host_replay_ready.ask_note(owed)
    else:
        seen, note = await _replay(ref, screen, body)
    if more and note:
        note += "; " + host_replay_ready.choose_note(more)
    return seen, note


async def run(ref: _Ref, screen: dict, fresh: bool = False) -> tuple:
    """``(screen, note)``: the screen after any saved flow that fit; ``note`` is empty if none ran."""
    body: dict = {}
    try:
        if not enabled():
            return screen, ""
        body = _body(ref, fresh)
        if _declined(ref, body) or not _seconds(ref.ctx):
            return screen, ""
        return await _fit_then_replay(ref, screen, body)
    except Exception:  # never stop a case for a replay
        logger.exception("mobile.host_auto_replay.run failed")
        body.pop("auto_trace", None)  # the device state is unknown: record nothing
        return screen, ""


def take_trace(run_id: str, tc_id: str) -> list:
    """The steps replayed for this case since the last call, marked as auto-replayed.

    Each entry keeps what the executor recorded (secrets already redacted) and gains
    ``source: "auto_replay"``; ``flow`` names the saved flow it came from. Taken once,
    so a step lands in the case trace exactly one time.
    """
    body = _BODIES.get((run_id, tc_id)) or {}
    taken = body.pop("auto_trace", None) or []
    return [dict(e, source=SOURCE) for e in taken if isinstance(e, dict)]


async def after_stop(step: _Step, ref: _Ref) -> tuple:
    """``(step, note)``: a stopped step's new screen after any saved flow that fit.

    The replayed steps join the step's trace, after the steps the host submitted.
    """
    if str(step.result.get("status") or "") != NEEDS_MODEL or not step.new_screen:
        return step, ""
    screen, note = await run(ref, step.new_screen)
    if not note:
        return step, note
    replayed = take_trace(step.run_id, step.tc_id)
    return step._replace(new_screen=screen, trace=step.trace + replayed), note


def annotate(payload: dict, note: str) -> None:
    """Put *note* on the packet inside a checkpoint payload, when there is both."""
    packet = (payload.get("content") or {}).get("packet")
    if note and isinstance(packet, dict):
        packet["auto_replay"] = note

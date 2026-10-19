"""Replay engine: wait for the next step's target after the screen changed (fix/1460 W3).

A script spans several screens. When the previous action changed the screen, the
next target may simply not have drawn yet; stopping at once costs the host a whole
turn. So the step first polls (event-driven, ``waits_poll._wait_for``) for up to
``TARGET_WAIT_S``, bounded by what is left of the submit budget. Never for the
first action of a submit (the host just saw that screen), never when the screen
did not change, and never for ``tap_text_if_present`` (it already polls). The
target still missing stops exactly as before, naming the wait.
"""

from __future__ import annotations

import time
from typing import Any

from tools.mobile.replay.context import _ReplayRun
from tools.mobile.replay.context_consts import TARGET_WAIT_S
from tools.mobile.replay.probe_gate import TEXT
from tools.mobile.replay.steps_elem import Aim, _resolve_element
from tools.mobile.replay.steps_notrun import WAITED_NO_ACTION
from tools.mobile.replay.steps_stop import StepAt, _close
from tools.mobile.replay.waits_cond import _needs_model_reply
from tools.mobile.replay.waits_poll import _wait_for
from tools.mobile.replay.waits_types import Probe

#: Ops that look nothing up on the screen (or judge a missing target themselves).
_NO_TARGET = frozenset(
    {"assert", "scroll", "done", "ask_tester", "tap_text_if_present"}
)


def _changed_before(run: _ReplayRun) -> bool:
    """True when the previous action of this script left a different screen."""
    if not run.trace:
        return False
    last = run.trace[-1]
    before, after = last.get("before_screen_hash"), last.get("after_screen_hash")
    return bool(before and after and before != after)


def _wants_target(cur: Any) -> bool:
    """Whether the step looks a target up (element, tap_text text, fill field)."""
    if cur.aim.op in _NO_TARGET or getattr(cur.aim.ref, "op", "") in _NO_TARGET:
        return False
    has_target = getattr(cur.aim.ref, "target", None) is not None
    return cur.aim.op in ("tap_text", "fill") or has_target


def _found(run: _ReplayRun, aim: Aim, at: StepAt) -> bool:
    """Whether the step's target resolves on ``at.screen``; changes nothing."""
    scratch = _ReplayRun(run.ctx, run.items, [])
    return _resolve_element(scratch, aim, at._replace(entry=dict(at.entry)))[2] is None


def _allowed_s(deadline: float) -> float:
    """The wait cap: ``TARGET_WAIT_S``, never past the submit budget."""
    return max(0.0, min(TARGET_WAIT_S, deadline - time.monotonic()))


def _interrupted(run: _ReplayRun, at: StepAt, why: str) -> dict:
    """Stop for the model: an interruption cut the wait short."""
    at.entry.update(outcome="interrupted", detail=why, **{WAITED_NO_ACTION: True})
    _close(run, at)
    return _needs_model_reply(run, at.screen, at.entry)


async def _poll(run: _ReplayRun, turn: Any, cur: Any) -> str:
    """Poll up to the allowed time for the target; the interruption text, ``""`` if none."""
    state, at = turn.state, cur.at._replace(screen=turn.state.screen)

    def met(_previous: object, current: object) -> bool:
        # ``_wait_for`` stretches its budget for a slow device (``scale_timeout``);
        # the submit budget is a hard stop, so the poll also ends once it is spent.
        if time.monotonic() >= state.deadline:
            return True
        return _found(run, cur.aim, at._replace(screen=current))

    run.ctx.wait_interruption = ""
    started = time.monotonic()
    label = "waiting for the next target (" + str(cur.aim.op) + ")"
    ms = max(1, int(_allowed_s(state.deadline) * 1000))
    probe = Probe(ms, met, label, TEXT)
    _ok, state.screen, _late = await _wait_for(run.ctx, state.screen, probe)
    at.entry["waited_for_target_s"] = round(time.monotonic() - started, 1)
    interruption, run.ctx.wait_interruption = run.ctx.wait_interruption, ""
    return interruption


async def wait_for_target(run: _ReplayRun, turn: Any, cur: Any) -> dict | None:
    """Poll for a missing target when it is due; the reply when an interruption cut it short."""
    state = turn.state
    if not (_wants_target(cur) and _changed_before(run)):
        return None
    at = cur.at._replace(screen=state.screen)
    if _allowed_s(state.deadline) <= 0 or _found(run, cur.aim, at):
        return None
    interruption = await _poll(run, turn, cur)
    if not interruption:
        return None
    return _interrupted(run, at._replace(screen=state.screen), interruption)


def note_wait(reply: dict, entry: dict) -> dict:
    """Name the wait in the ``missing_element`` stop that followed it."""
    waited = entry.get("waited_for_target_s")
    content = reply.get("content")
    if waited is None or entry.get("outcome") != "missing_element":
        return reply
    note = " (waited " + format(float(waited), ".1f") + " s)"
    entry["detail"] = str(entry.get("detail") or "") + note
    if isinstance(content, dict):
        content["reason"] = str(content.get("reason") or "") + note
        _patch_trace(content, entry["index"], note)
    return reply


def _patch_trace(content: dict, index: int, note: str) -> None:
    """The reply holds a redacted COPY of the trace: name the wait there too."""
    for copy in content.get("trace") or []:
        if copy.get("index") == index and copy.get("outcome") == "missing_element":
            copy["detail"] = str(copy.get("detail") or "") + note

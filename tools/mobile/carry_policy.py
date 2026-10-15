"""May the next call reuse the screen the last call already read?

``executor.Context.carry_screen`` lets a replay start on the screen the device
last dumped instead of dumping again. That is only safe when nothing happened
in between, so the answer is derived from the one fact that proves it: the
previous turn LEFT A TRACE (it ran to a reply the model read). An actuating
turn ends on the settled screen its last op dumped, and that dump is carried
(``dump_carry._remember``); the next call takes it only through the live
freshness guard in ``dump_carry._take_carried`` (young, same package, same
focused activity, used once), so a screen the phone has left is never reused.
An empty trace, a missing trace or an unknown run says no.

The memory is per run id and bounded: at most :data:`MAX_CARRY_RUNS` runs are
remembered and the oldest is dropped first, so a long-lived server does not
grow one entry per run it ever served.

Pure: no disk, no device, no model.
"""

from __future__ import annotations

from collections import OrderedDict

from tools.mobile.replay.guard import NON_ACTUATING_OPS

#: Runs whose last-turn answer is kept for this process; the oldest goes first.
MAX_CARRY_RUNS = 64

inert: OrderedDict[str, bool] = OrderedDict()


def _op_of(entry: object) -> object:
    action = entry.get("action") if isinstance(entry, dict) else None
    return action.get("op") if isinstance(action, dict) else None


def trace_is_inert(trace: object) -> bool:
    """True only for a non-empty trace whose every op cannot actuate."""
    if not isinstance(trace, list) or not trace:
        return False
    return all(_op_of(entry) in NON_ACTUATING_OPS for entry in trace)


def trace_is_known(trace: object) -> bool:
    """True for a non-empty trace of entries: the turn ran and its reply was read."""
    if not isinstance(trace, list) or not trace:
        return False
    return all(isinstance(entry, dict) for entry in trace)


def note(run_id: str, trace: object) -> None:
    """Record whether the turn that just ran left a screen worth carrying."""
    key = str(run_id or "")
    inert[key] = trace_is_known(trace)
    inert.move_to_end(key)
    while len(inert) > MAX_CARRY_RUNS:
        inert.popitem(last=False)


def allowed(run_id: str) -> bool:
    """May this run's next call start from the screen already read?"""
    return inert.get(str(run_id or ""), False)

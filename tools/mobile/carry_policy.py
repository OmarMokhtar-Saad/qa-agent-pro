"""May the next call reuse the screen the last call already read?

``executor.Context.carry_screen`` lets a replay start on the screen the device
last dumped instead of dumping again. That is only safe when nothing happened
in between, so the answer is derived from the one fact that proves it: the
previous turn's trace held ONLY ops that cannot actuate
(``executor.NON_ACTUATING_OPS``: an assert, ``done``, ``ask_tester``).
An empty trace, a missing trace, an unknown run, or any actuating op says no.

The memory is per run id and bounded: at most :data:`MAX_CARRY_RUNS` runs are
remembered and the oldest is dropped first, so a long-lived server does not
grow one entry per run it ever served.

Pure: no disk, no device, no model.
"""

from __future__ import annotations

from collections import OrderedDict

from tools.mobile import executor

#: Runs whose last-turn answer is kept for this process; the oldest goes first.
MAX_CARRY_RUNS = 64

_inert: OrderedDict[str, bool] = OrderedDict()


def _op_of(entry: object) -> object:
    action = entry.get("action") if isinstance(entry, dict) else None
    return action.get("op") if isinstance(action, dict) else None


def trace_is_inert(trace: object) -> bool:
    """True only for a non-empty trace whose every op cannot actuate."""
    if not isinstance(trace, list) or not trace:
        return False
    return all(_op_of(entry) in executor.NON_ACTUATING_OPS for entry in trace)


def note(run_id: str, trace: object) -> None:
    """Record whether the turn that just ran left the screen untouched."""
    key = str(run_id or "")
    _inert[key] = trace_is_inert(trace)
    _inert.move_to_end(key)
    while len(_inert) > MAX_CARRY_RUNS:
        _inert.popitem(last=False)


def allowed(run_id: str) -> bool:
    """May this run's next call start from the screen already read?"""
    return _inert.get(str(run_id or ""), False)

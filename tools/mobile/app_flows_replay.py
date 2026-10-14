"""Reading a finished replay back: did it run as saved, and where did it stop."""

from __future__ import annotations

from typing import Any

from tools.mobile.app_flows_consts import OK_OUTCOMES


def clean_replay(case: Any, count: int) -> bool:
    """True when the LAST *count* trace entries all ran as planned.

    The trace is merged across submits, so only the tail belongs to this replay.
    """
    trace = case.get("trace") if isinstance(case, dict) else None
    if not isinstance(trace, list) or count < 1 or len(trace) < count:
        return False
    return all(
        isinstance(entry, dict) and str(entry.get("outcome") or "") in OK_OUTCOMES
        for entry in trace[-count:]
    )


def stop_line(name: str, case: Any, count: int) -> str:
    """The sentence for a replay that did not finish as saved. Never says a step was skipped."""
    trace = case.get("trace") if isinstance(case, dict) else None
    tail = list(trace[-count:]) if isinstance(trace, list) and count > 0 else []
    step = len(tail) + 1
    for index, entry in enumerate(tail, 1):
        if not (
            isinstance(entry, dict) and str(entry.get("outcome") or "") in OK_OUTCOMES
        ):
            step = index
            break
    return (
        "Flow `%s` stopped at step %d of %d: the screen no longer matches the saved steps. "
        "Nothing was tapped blind; carry on from the packet below, and save the flow again "
        "if the app changed." % (name, min(step, count), count)
    )

"""Bookkeeping per event: which app run and which turn an event belongs to."""

from __future__ import annotations

from tools.mobile_evidence.grammar_state import _Line, _ParseState


def _enter_run(state: _ParseState, ev) -> None:
    """At a ``runIndex`` change, forget the open calls of the run before."""
    if ev.get("runIndex") != state.run_index:
        state.run_index = ev.get("runIndex")
        state.open_binding, state.open_llm, state.last_usage = {}, None, None


def _new_run(state: _ParseState, line: _Line) -> dict:
    """The fresh record of an app run, opened at ``line``."""
    return {
        "appRunId": line.run,
        "runIndex": state.run_index,
        "firstSeq": line.seq,
        "lastSeq": line.seq,
        "firstTs": line.ts,
        "lastTs": line.ts,
        "llm": 0,
        "bindings": 0,
        "tools": 0,
        "errors": 0,
        "turns": 0,
    }


def _widen_window(r: dict, ts) -> None:
    """Stretch the run's clock window to ``ts``; a stamp that will not compare changes
    only what was set before the comparison raised."""
    try:
        if r["firstTs"] is None or ts < r["firstTs"]:
            r["firstTs"] = ts
        if r["lastTs"] is None or ts > r["lastTs"]:
            r["lastTs"] = ts
    except TypeError:
        pass


def _record_run(state: _ParseState, line: _Line) -> None:
    """Open or extend the app run record of ``line`` and clock its timestamp."""
    run, seq, ts = line.run, line.seq, line.ts
    if not run:
        return
    r = state.runs.setdefault(run, _new_run(state, line))
    r["lastSeq"] = seq
    # A structured log carries epoch ms; a logcat capture carries the raw
    # ``MM-DD HH:MM:SS.mmm`` stamp, which orders lexicographically within a
    # year and is made numeric by ``evidence.normalise_clock``. Either way the
    # clock is recorded, so a logcat-only run still has a window.
    if isinstance(ts, (int, float, str)) and not isinstance(ts, bool) and ts != "":
        _widen_window(r, ts)
        state.clock.setdefault(run, {})[str(seq)] = ts


def _record_turn(state: _ParseState, line: _Line) -> None:
    """Open or extend the turn record of ``line``."""
    turn, seq = line.turn, line.seq
    if not turn:
        return
    t = state.turns.setdefault(
        turn,
        {
            "turnId": turn,
            "connectionId": line.ev.get("connectionId"),
            "appRunId": line.run,
            "firstSeq": seq,
            "lastSeq": seq,
            "llm": 0,
            "bindings": 0,
            "tools": 0,
            "errors": 0,
        },
    )
    t["lastSeq"] = seq

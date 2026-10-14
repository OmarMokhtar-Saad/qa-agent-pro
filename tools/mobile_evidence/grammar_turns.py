"""Turn attribution: which turn every stream item belongs to."""

from __future__ import annotations

from typing import NamedTuple

_COUNTED = ("llm", "bindings", "tools")
_STREAM_KEYS = (
    "llm",
    "bindings",
    "tools",
    "answers",
    "flowStates",
    "cards",
    "notes",
    "runlog",
)


def turn_of_seq(parsed, seq, app_run=None):
    """Which turn a ``seq`` falls in, for a stream ``parse`` never saw."""
    if seq is None:
        return None
    for t in parsed.get("turns") or []:
        if app_run is not None and t.get("appRunId") != app_run:
            continue
        if (t.get("firstSeq") or 0) <= seq <= (t.get("lastSeq") or 0):
            return t["turnId"]
    return None


def _bounded_turn(bounds_by_run: dict, run, seq):
    """The turn id whose bounds hold ``seq`` in ``run``, or None."""
    if seq is None:
        return None
    for start, end, tid in bounds_by_run.get(run) or ():
        if seq >= start and (end is None or seq <= end):
            return tid
    return None


def _run_of(x: dict):
    return x.get("appRunId") or (x.get("detail") or {}).get("appRunId")


class _Turns(NamedTuple):
    """The bounds of every turn per app run, and the fresh record of each turn."""

    bounds_by_run: dict
    turns: dict


def _run_bounds(run, utts: list) -> list:
    """The (start, end, turnId) bounds of one app run's utterances, sorted in place."""
    utts.sort(key=lambda u: u.get("seq") or 0)
    bounds = []
    for i, u in enumerate(utts):
        start = u.get("seq") or 0
        end = (utts[i + 1].get("seq") or 0) - 1 if i + 1 < len(utts) else None
        tid = "%s/t%d" % (run, i + 1) if run else "turn-%d" % (i + 1)
        bounds.append((start, end, tid))
    return bounds


def _turn_record(run, bound: tuple, index: int) -> dict:
    """The fresh record of the turn that starts at ``bound``."""
    start, _end, tid = bound
    return {
        "turnId": tid,
        "connectionId": None,
        "appRunId": run,
        "index": index,
        "firstSeq": start,
        "lastSeq": start,
        "llm": 0,
        "bindings": 0,
        "tools": 0,
        "errors": 0,
    }


def _turn_bounds(by_run: dict):
    """Per app run the (start, end, turnId) bounds, and the fresh turn record of each."""
    bounds_by_run, turns = {}, {}
    for run, utts in by_run.items():
        bounds_by_run[run] = _run_bounds(run, utts)
        for i, bound in enumerate(bounds_by_run[run], 1):
            turns[bound[2]] = _turn_record(run, bound, i)
    return _Turns(bounds_by_run, turns)


def _stamp_item(item: dict, key: str, tb: _Turns) -> None:
    """Write ``turnId`` on one stream item and keep its turn's counts and last seq."""
    tid = _bounded_turn(tb.bounds_by_run, _run_of(item), item.get("seq"))
    item["turnId"] = tid
    if tid:
        t = tb.turns[tid]
        t["lastSeq"] = max(t["lastSeq"], item.get("seq") or 0)
        if key in _COUNTED:
            t[key] += 1


def _stamp_streams(parsed: dict, tb: _Turns) -> None:
    for key in _STREAM_KEYS:
        for x in parsed.get(key) or []:
            _stamp_item(x, key, tb)


def _stamp_utterances(by_run: dict, tb: _Turns) -> None:
    for utts in by_run.values():
        for u in utts:
            u["turnId"] = _bounded_turn(
                tb.bounds_by_run, u.get("appRunId"), u.get("seq")
            )


def _stamp_errors(parsed: dict, tb: _Turns) -> None:
    for err in parsed.get("errors") or []:
        tid = _bounded_turn(tb.bounds_by_run, _run_of(err), err.get("seq"))
        err["turnId"] = tid
        if tid:
            tb.turns[tid]["errors"] += 1


def _stamp_turn_ids(parsed: dict, by_run: dict, tb: _Turns) -> None:
    """Write ``turnId`` on every stream item and keep each turn's counts and last seq."""
    _stamp_streams(parsed, tb)
    _stamp_utterances(by_run, tb)
    _stamp_errors(parsed, tb)


def _group_utterances(parsed: dict) -> dict:
    """The utterances of a capture, by app run."""
    by_run: dict = {}
    for u in parsed["utterances"]:
        by_run.setdefault(u.get("appRunId"), []).append(u)
    return by_run


def _order_turns(parsed: dict, turns: dict) -> list:
    """The turn records in app-run order, then by first seq."""
    order = {
        r.get("appRunId"): r.get("runIndex") for r in (parsed.get("appRuns") or [])
    }
    return sorted(
        turns.values(),
        key=lambda t: (
            order.get(t["appRunId"]) if order.get(t["appRunId"]) is not None else -1,
            t["firstSeq"],
        ),
    )


def _count_run_turns(parsed: dict) -> None:
    """Write how many turns each app run has."""
    per_run: dict = {}
    for t in parsed["turns"]:
        per_run[t["appRunId"]] = per_run.get(t["appRunId"], 0) + 1
    for r in parsed.get("appRuns") or []:
        r["turns"] = per_run.get(r.get("appRunId"), 0)


def attribute_turns(parsed):
    """Turn boundaries from the SDK's own turn-start marker, when it stamped no turnId.

    One app run at a time: ``seq`` restarts per run. Everything between one marker and the
    next belongs to that turn; everything before a run's first marker is start-up and
    stays unattributed. A capture with real turn ids is left exactly as it is.
    """
    if parsed.get("turns") or not parsed.get("utterances"):
        return parsed
    by_run = _group_utterances(parsed)
    tb = _turn_bounds(by_run)
    _stamp_turn_ids(parsed, by_run, tb)
    parsed["turns"] = _order_turns(parsed, tb.turns)
    _count_run_turns(parsed)
    parsed["turnsFromNarration"] = True
    return parsed

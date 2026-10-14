"""Logged clocks and the durations derived from them, for the evidence join."""

from __future__ import annotations


def _ts_of(report, run, seq):
    value = (report.get("clock") or {}).get(run, {}).get(str(seq))
    return value if isinstance(value, (int, float)) else None


def _derived_ms(report, run, rec):
    """How long a request took, DERIVED from two logged clocks -- never measured."""
    a = _ts_of(report, run, rec.get("seq"))
    b = _ts_of(report, run, rec.get("endSeq"))
    if a is None or b is None or b < a:
        return None
    return b - a


def _stamped(report, run, rec):
    return dict(
        rec,
        ts=_ts_of(report, run, rec.get("seq")),
        derivedMs=_derived_ms(report, run, rec),
    )


def _collect_record_spans(report, out, owned):
    """Derived ms of every llm / api / tool record of a paired turn, into ``out``."""
    for kind, stream in (("llm", "llm"), ("api", "bindings"), ("tool", "tools")):
        for rec in report.get(stream) or []:
            if not owned(rec):
                continue
            ms = _derived_ms(report, rec.get("appRunId"), rec)
            if ms is None:
                continue
            if kind == "llm" and rec.get("error"):
                out["llmfail"].append(float(ms))
            else:
                out[kind].append(float(ms))


def _turn_clocks(report, stream, mine):
    """``(turnId, clock)`` of every record of ``stream`` on a paired turn, in order."""
    for rec in report.get(stream) or []:
        if rec.get("turnId") in mine:
            yield rec["turnId"], _ts_of(report, rec.get("appRunId"), rec.get("seq"))


def _first_clock_by_turn(report, stream, mine):
    """``turnId`` -> the clock of its FIRST record in ``stream``, for paired turns only."""
    found = {}
    for tid, ts in _turn_clocks(report, stream, mine):
        found.setdefault(tid, ts)
    return found


def _last_clock_by_turn(report, stream, mine):
    """``turnId`` -> the clock of its LAST record in ``stream``, for paired turns only."""
    found = {}
    for tid, ts in _turn_clocks(report, stream, mine):
        found[tid] = ts
    return found


def _collect_turn_spans(report, out, mine):
    """Utterance -> answer (or error) durations of every paired turn, into ``out``."""
    said = _last_clock_by_turn(report, "utterances", mine)
    heard = _first_clock_by_turn(report, "answers", mine)
    threw = _last_clock_by_turn(report, "errors", mine)
    for tid, start in said.items():
        if start is None:
            continue
        end, bucket = heard.get(tid), "turn"
        if end is None:
            end, bucket = threw.get(tid), "turnfail"
        if end is not None and end >= start:
            out[bucket].append(float(end - start))

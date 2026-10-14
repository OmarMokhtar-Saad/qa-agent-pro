"""Place one app turn into a case window, or record why it could not be."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile_evidence.evidence_clock import stamp
from tools.mobile_evidence.evidence_notes import AMBIGUOUS_NOTE
from tools.mobile_evidence.evidence_timing import _ts_of


class _PlaceCtx(NamedTuple):
    report: dict
    timed: list
    by_tc: dict
    said: dict


def _typed_texts(case):
    """Typed literals a trace may carry. The lane never stores them; this stays empty."""
    out = []
    for entry in (case or {}).get("trace") or []:
        action = (entry or {}).get("action") if isinstance(entry, dict) else None
        if isinstance(action, dict) and action.get("op") == "type":
            text = action.get("text")
            if isinstance(text, str) and text and text != "***":
                out.append(text.strip())
    return out


def _tally_text(acc, typed, heard):
    """Count one claimed turn as confirmed, unconfirmed or mismatched."""
    if not typed:
        acc["unconfirmed"] += 1
    elif heard in typed:
        acc["confirmed"] += 1
    else:
        acc["mismatched"] += 1


def _claim(ctx, hit, acc):
    """Pair a turn with the one case that owns it, and confirm its text if it can be."""
    tc_id, tid = hit
    acc["pairs"].append((tc_id, tid))
    acc["claimed"].add(tc_id)
    _tally_text(acc, _typed_texts(ctx.by_tc.get(tc_id)), ctx.said.get(tid, ""))


def _unplaced(timed, a, b):
    """``(bucket, why)`` for a turn that overlaps no window."""
    if timed and a > timed[0]["lo"] and b < timed[-1]["hi"]:
        return "ambiguous", AMBIGUOUS_NOTE
    return "outside", "served at %s, outside every case window of this run" % stamp(a)


def _place_turn(ctx, t, acc):
    """Attribute one turn to a window, or record why it could not be."""
    run = t.get("appRunId")
    a = _ts_of(ctx.report, run, t.get("firstSeq"))
    b = _ts_of(ctx.report, run, t.get("lastSeq"))
    tid = t.get("turnId")
    if a is None or b is None:
        acc["unclocked"].append(tid)
        return
    hits = [w for w in ctx.timed if a <= w["hi"] and b >= w["lo"]]
    if len(hits) == 1:
        _claim(ctx, (hits[0]["tc_id"], tid), acc)
    elif len(hits) > 1:
        why = AMBIGUOUS_NOTE + " (overlaps %d windows)" % len(hits)
        acc["ambiguous"].append((tid, why))
    else:
        bucket, why = _unplaced(ctx.timed, a, b)
        acc[bucket].append((tid, why))

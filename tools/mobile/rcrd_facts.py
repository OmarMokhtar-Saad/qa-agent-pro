"""Per-case facts for the report: everything a card, a table row, a chip and a KPI need, computed once."""

from __future__ import annotations

from tools.mobile import run_store
from tools.mobile.rcrd_read import _capture_of, _case_wall, _expected_of, _network_of
from tools.mobile.rcrd_screens import _case_screens
from tools.mobile.report_text import _lat_bucket, _slug, _text
from tools.mobile.report_trace import _trace_rows
from tools.mobile.report_verdict import _planned_case, _verdict_of
from tools.mobile_evidence import crash_detector


# The identity, verdict and evidence a case checkpoint carries.
#
# Read through the detector's own accessor, never by walking the record
# here: two walks are two derivations of "where a crash lives".
#
# The wire this case reached, read off the checkpoint through ONE
# accessor. A second walk of the record inside the card would be a
# second derivation of where the network record lives, and mirrored
# conditions drift. Absent on a checkpoint written before this feature,
# which is an empty dict the renderer states rather than a crash.
#
# This case's own API-capture flows (Phase 7, T7.1), read from
# evidence/ -- never from the checkpoint, which only carries this
# case's flow COUNT via ladder.record, not the flows themselves.
def _case_record(safe: dict, tc_id: str, run_id: str) -> dict:
    """The identity, verdict and evidence a case checkpoint carries."""
    return {
        "tc_id": tc_id,
        "slug": _slug(tc_id),
        "title": _text(safe.get("title"), 120),
        "verdict": _verdict_of(safe),
        "crash": crash_detector.crash_of_case(safe),
        "network": _network_of(safe),
        "capture": _capture_of(run_id, tc_id),
        "status": _text(safe.get("status"), 40),
        "reason": _text(safe.get("reason"), 400),
        "finding": _text(safe.get("finding"), 200),
    }


# The case's escape, uncharged-stop and planning-turn counts.
#
# Planning turns by the tester's own chat model: the first plan, every
# escape-hatch re-plan, AND every uncharged stale-selector stop. A case
# still planning has taken none yet.
#
# `free_stops` was written to the checkpoint by `case_runner` and read by
# NOTHING -- not this module, not the submit reply, not `qa_mobile_status`
# -- so a case that spent two uncharged stops and one escape reported two
# planning turns for four round trips. The disclosure is this number,
# which the card already showed, rather than a new column: a count that is
# wrong is worse than one that is missing.
def _case_planning(safe: dict, rows: list) -> dict:
    """The case's escape, uncharged-stop and planning-turn counts."""
    try:
        escapes = int(safe.get("escapes") or 0)
    except (TypeError, ValueError, OverflowError):
        escapes = 0
    try:
        free_stops = max(0, int(safe.get("free_stops") or 0))
    except (TypeError, ValueError, OverflowError):
        free_stops = 0
    return {
        "escapes": max(0, escapes),
        "free_stops": free_stops,
        "plans": (1 + max(0, escapes) + free_stops) if rows else 0,
        "updated": safe.get("updated"),
        "rows": rows,
    }


def _case_planned(planned: dict, rows: list) -> dict:
    """What the suite planned for the case, and how long it took."""
    wall = _case_wall(rows)
    return {
        "wall": wall,
        "lat": _lat_bucket(wall),
        "module": _text(planned.get("module"), 60) or "(unfiled)",
        "priority": _text(planned.get("priority"), 24) or "(unset)",
        "type": _text(planned.get("type"), 24) or "(unset)",
        "expected": _expected_of(planned),
    }


def _case_facts(case: object, manifest: dict, run_id: str = "") -> dict:
    """Everything a card, a table row, a chip and a KPI need, computed ONCE."""
    raw = case if isinstance(case, dict) else {}
    safe = run_store.redact(raw)
    safe = safe if isinstance(safe, dict) else {}
    tc_id = _text(safe.get("tc_id"), 40)
    rows = _trace_rows(safe.get("trace"))
    facts = _case_record(safe, tc_id, run_id)
    facts.update(_case_planning(safe, rows))
    facts.update(_case_screens(safe, rows))
    facts.update(_case_planned(_planned_case(manifest, tc_id), rows))
    return facts

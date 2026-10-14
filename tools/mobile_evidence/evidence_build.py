"""The entry point: parse (or take) the app report and join it to the run's cases."""

from __future__ import annotations

from typing import NamedTuple

from config.settings_guard import never_raise
from tools.mobile_evidence import grammar
from tools.mobile_evidence.evidence_clock import capture_offset, normalise_clock
from tools.mobile_evidence.evidence_join import join
from tools.mobile_evidence.evidence_run import Evidence, EvidenceFrame
from tools.mobile_evidence.evidence_windows import case_windows, fence


class _BuildSrc(NamedTuple):
    profile: object
    compiled: object
    report: object
    ndjson: object
    logcat: object


class BuildInputs(NamedTuple):
    """The optional report and captures a build starts from."""

    report: object = None
    ndjson: object = None
    logcat: object = None


class _Run(NamedTuple):
    cases: object
    windows: list
    offset: int
    note: str


def empty_report():
    """The report of a run whose capture was never recorded: no streams, ``noCapture``."""
    return {
        "schema": grammar.SCHEMA,
        "run": {"source": None, "noCapture": True},
        "appRuns": [],
    }


_NO_CAPTURE_NOTE = (
    "this run holds no app capture -- no structured log and no logcat were "
    "recorded -- so what the lane did is shown and what the app did is not "
    "available for any case"
)


def _capture(src):
    """``(report, note, error)`` of a run handed no report: the empty one, or the parsed logs."""
    if src.ndjson is None and src.logcat is None:
        return empty_report(), _NO_CAPTURE_NOTE, None
    parsed = grammar.build(
        grammar.GrammarKit(src.profile, src.compiled),
        grammar.CaptureSources(src.ndjson, src.logcat),
    )
    if parsed.get("error"):
        return None, "", parsed["error"]
    return parsed["content"], "", None


def _resolve_report(src):
    """``(report, note, error)``: the report given, parsed, or the empty one."""
    report, note = src.report, ""
    if report is None:
        report, note, error = _capture(src)
        if error:
            return None, "", error
    if not isinstance(report, dict):
        return None, "", "the app report is not a mapping"
    return report, note, None


def _latest_run_end(report):
    """The latest ``lastTs`` over the app runs, or None when none is a number."""
    anchor = None
    for r in report.get("appRuns") or []:
        ts = r.get("lastTs")
        if isinstance(ts, (int, float)):
            anchor = ts if anchor is None else max(anchor, ts)
    return anchor


def _anchor(report, timed):
    """The clock the logcat stamps are placed against: the last app run, else the last case."""
    anchor = _latest_run_end(report)
    if anchor is None:
        anchor = timed[-1]["hi"] if timed else None
    return anchor


def _run_window(timed, offset):
    """The device-clock span the run's timed cases cover."""
    return {
        "from": min(w["lo"] for w in timed) if timed else None,
        "to": max(w["hi"] for w in timed) if timed else None,
        "cases": len(timed),
        "offsetMs": offset,
    }


def _joined(report, run):
    """Fence the app runs, join the report to the cases and wrap it as an Evidence."""
    timed = [w for w in run.windows if w.get("lo") is not None]
    normalise_clock(report, run.offset, _anchor(report, timed))
    included, excluded, fence_note = fence(report, _run_window(timed, run.offset))
    if (report.get("run") or {}).get("noCapture"):
        fence_note = run.note or fence_note
    jn = join(report, run.cases, run.windows)
    frame = EvidenceFrame(run.cases, run.windows, included, excluded, fence_note)
    return Evidence(report, frame, jn)


def build(cases, kit, inputs=BuildInputs()) -> dict:
    """Parse (or take) the app report and join it to ``cases``. Never raises.

    ``{"error", "content": Evidence}``. A run with no capture at all yields an Evidence
    over :func:`empty_report`, whose fence note says so -- the page then draws the lane's
    own rows and nothing else.
    """
    # noqa: BLE001 - the contract is never to raise
    with never_raise() as g:
        src = _BuildSrc(kit.profile, kit.compiled, *inputs)
        report, note, error = _resolve_report(src)
        if error:
            return {"error": error, "content": None}
        offset = capture_offset(inputs.ndjson) if inputs.ndjson is not None else 0
        run = _Run(cases, case_windows(cases), offset, note)
        return {"error": None, "content": _joined(report, run)}
    return {
        "error": "could not join the capture to the run: " + str(g.exc)[:200],
        "content": None,
    }

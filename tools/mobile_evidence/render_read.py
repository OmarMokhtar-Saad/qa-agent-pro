"""Reading a run's evidence files within the load budget (review M3).

The ONLY ``tools.mobile`` import of the evidence fragments lives here.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from tools import settings_port
from tools.mobile import run_store
from tools.mobile_evidence import render_esc as esc


class Run(NamedTuple):
    """What the loader was asked about: the run, its manifest, cases and profile."""

    run_id: str
    manifest: dict
    cases: list
    profile: object


class Sources(NamedTuple):
    """The event-log text (or None), the logcat lines and the skipped slice names."""

    ndjson: str | None
    logcat: list
    skipped: list


def none_content(reason: str, cases: list, extra: dict | None = None) -> dict:
    content: dict[str, Any] = {
        "evidence": None,
        "report": None,
        "source": "none",
        "reason": str(reason),
        "disabled": [],
        "integrity": {},
        "profile": None,
        "cases_by_id": {
            str(c.get("tc_id") or ""): c for c in cases if isinstance(c, dict)
        },
        "by_case": {},
        "clock_missing": [],
    }
    content.update(extra or {})
    return {"error": None, "content": content}


def flatten_case(case: dict) -> dict:
    """The join reads ``clock_offset_ms`` at the top; the lane stores it under ``evidence``."""
    out = dict(case)
    ev: Any = case.get("evidence") if isinstance(case.get("evidence"), dict) else {}
    if "clock_offset_ms" not in out:
        out["clock_offset_ms"] = ev.get("clock_offset_ms")
    return out


def _slice_names(files: dict) -> list:
    """(case id, slice name) in case order, so one slice is in memory at a time."""
    return [
        (tc_id, name)
        for tc_id in sorted(k for k in files if k)
        for name in sorted(files.get(tc_id) or [])
        if name.startswith("logcat-") and name.endswith(".txt")
    ]


def _slice_text(run_id: str, tc_id: str, name: str) -> str:
    text = run_store.read_evidence_text(run_id, tc_id, name).get("content")
    return text if isinstance(text, str) and text.strip() else ""


def read_logcat_slices(run_id: str, files: dict, budget: int) -> tuple[list, list]:
    """(logcat lines, skipped slice names): what is skipped is NAMED, never absent."""
    lines: list = []
    skipped: list = []
    for tc_id, name in _slice_names(files):
        label = str(tc_id) + "/" + str(name)
        if budget <= 0:
            skipped.append(label)
            continue
        text = _slice_text(run_id, tc_id, name)
        size = len(text.encode("utf-8", errors="replace"))
        if size > budget:
            skipped.append(label)
            budget = 0
        else:
            budget -= size
            lines.extend(text.splitlines())
    return lines, skipped


def _list_files(run: Run) -> tuple[Any, dict | None]:
    listed = run_store.list_evidence(run.run_id)
    files = listed.get("content") if isinstance(listed, dict) else None
    if listed.get("error") or not isinstance(files, dict):
        return None, none_content(
            "no app log captured: " + str(listed.get("error") or "no evidence dir"),
            run.cases,
        )
    return files, None


def _no_profile(run: Run, package: str) -> dict:
    """The no-profile refusal; a logcat-only capture is named, not denied."""
    name = package or "(no package)"
    if run.manifest.get("events_source") == "logcat-only":
        reason = "no profile for " + name
        return none_content(reason, run.cases, {"events_source": "logcat-only"})
    reason = "no app log captured for " + name + ": no profile for this package"
    return none_content(reason, run.cases)


def evidence_files(run: Run) -> tuple[Any, dict | None]:
    """(files, None) when the run can be read, else (None, the refusal)."""
    package = str(run.manifest.get("package") or "")
    if not getattr(settings_port.get(), "qa_mobile_app_evidence", True):
        return None, none_content(esc.FLAG_OFF_REASON, run.cases)
    if run.profile is None:
        return None, _no_profile(run, package)
    return _list_files(run)


def read_sources(run_id: str, files: dict) -> Sources:
    """The event log and the logcat slices within the load budget."""
    ndjson_text = None
    budget = int(esc.MAX_LOAD_BYTES)
    if esc.EVENTS_FILE in (files.get("") or []):
        read = run_store.read_evidence_text(run_id, None, esc.EVENTS_FILE)
        if isinstance(read.get("content"), str) and read["content"].strip():
            ndjson_text = read["content"]
            budget -= len(ndjson_text.encode("utf-8", errors="replace"))
    logcat_lines, skipped_slices = read_logcat_slices(run_id, files, budget)
    return Sources(ndjson_text, logcat_lines, skipped_slices)

"""Per-case flows: read the addon's raw JSONL, redact at WRITE time, cap, and
attach to the run's evidence (T5.3, T5.4).

**Routing.** The addon (``addon.py``, inside mitmdump) appends one raw line per
flow to the file its CURRENT-CASE pointer names. :func:`mark_case_current` /
:func:`mark_case_finished` are the ONE producer of that pointer; flows outside
any case land in the run's OUTSIDE file, never in the nearest case.

**Redaction happens HERE, at write time, never in the addon.** The pattern set
is ``tools.mobile_shared.scrub``'s, armed for ONE case through
:func:`scrub.armed_scope` with the tester's typed values; no mask is
reimplemented. The raw JSONL is DELETED once redacted, on every exit; only the
redacted document lives under the run's evidence directory.
"""

from __future__ import annotations

import json
import logging
import os  # noqa: F401  (kept on the facade: flows.os)
from pathlib import Path

from tools.mobile import run_store
from tools.mobile_capture import paths  # noqa: F401  (kept on the facade: flows.paths)
from tools.mobile_capture.flows_ptr import (  # noqa: F401
    _jsonl_path,
    _pointer_path,
    mark_case_current,
    mark_case_finished,
)
from tools.mobile_capture.flows_redact import (  # noqa: F401
    MAX_BODY_BYTES,
    MAX_HEADER_CHARS,
    MAX_URL_CHARS,
    TRUNCATED_MARKER,
    _redact_flow,
    _redact_headers,
    _redact_url,
    _truncate_text,
    redact_body,
)
from tools.mobile_shared import scrub

logger = logging.getLogger(__name__)

#: The evidence document name, beside the pcap lane's own files under
#: ``evidence/<tc_id>/`` (or ``evidence/`` at the run level, for the outside
#: bucket -- ``run_store.write_evidence_json``'s own ``tc_id=None`` shape).
EVIDENCE_NAME = "capture_flows.json"

#: The run-level bucket key for flows outside any case's window.
OUTSIDE_KEY = "outside"

#: Rows on ONE case card, each carrying two collapsible bodies.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_FLOWS_PER_CASE = 300


#: ALL bodies for one run in that same single file -- the batch bound the
#: per-flow cap above cannot give.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_TOTAL_BODY_BYTES = 4 * 1024 * 1024

#: Raw bytes this module will read from ONE jsonl file. Measured at the LAST
#: consumer: the addon's own stop is best-effort in a process this server
#: does not control, so THIS is the bound that actually protects the reader.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_JSONL_BYTES = 32 * 1024 * 1024

BUDGET_OMITTED = "<omitted: total body budget for this run reached>"


def _over_cap(consumed: int, path: Path) -> bool:
    if consumed <= MAX_JSONL_BYTES:
        return False
    logger.warning(
        "mobile_capture.flows: %s exceeds %d bytes; stopping early",
        path,
        MAX_JSONL_BYTES,
    )
    return True


def read_raw_lines(path: Path) -> list:
    """Every parseable line of *path*, up to :data:`MAX_JSONL_BYTES` of raw bytes.

    The LAST consumer of the raw file; malformed lines are skipped, never raised.
    """
    out: list = []
    if not path.is_file():
        return out
    consumed = 0
    with open(path, "rb") as handle:
        for raw_line in handle:
            consumed += len(raw_line)
            if _over_cap(consumed, path):
                break
            try:
                out.append(json.loads(raw_line.decode("utf-8", errors="replace")))
            except (ValueError, TypeError):
                continue
    return out


def _budgeted(raw_flows: list) -> list:
    """Redact up to the count cap, omitting bodies past the total budget."""
    flows: list = []
    total_body_bytes = 0
    for raw in raw_flows[:MAX_FLOWS_PER_CASE]:
        record = _redact_flow(raw)
        if record is None:
            continue
        if total_body_bytes + record["_body_bytes"] > MAX_TOTAL_BODY_BYTES:
            record["request_body"] = BUDGET_OMITTED
            record["response_body"] = BUDGET_OMITTED
            record["truncated"] = True
        else:
            total_body_bytes += record["_body_bytes"]
        record.pop("_body_bytes", None)
        flows.append(record)
    return flows


def _remove_raw(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("mobile_capture.flows: could not remove raw %s", path)


def _write_doc(run_id: str, key: str, payload: dict) -> dict:
    tc_id = None if key == OUTSIDE_KEY else key
    write = run_store.write_evidence_json(
        run_store.EvidenceRef(run_id, tc_id, EVIDENCE_NAME), payload
    )
    if write.get("error"):
        return {"error": write["error"], "content": None}
    return {"error": None, "content": payload}


def _process(run_id: str, key: str, path: Path) -> dict:
    """Redact one raw jsonl into an evidence document, delete it. Never raises."""
    try:
        raw_flows = read_raw_lines(path)
        flows = _budgeted(raw_flows)
        payload = {
            "flow_count": len(flows),
            "flows": flows,
            "truncated_by_count": len(raw_flows) > MAX_FLOWS_PER_CASE,
        }
        reply = _write_doc(run_id, key, payload)
        _remove_raw(path)
        return reply
    except Exception as exc:
        logger.exception("mobile_capture.flows._process failed for %s", key)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"error": str(exc), "content": None}


def _typed_values(tester_inputs: object) -> list:
    return (
        list((tester_inputs or {}).values()) if isinstance(tester_inputs, dict) else []
    )


def finish_case(run_id: str, tc_id: str, *, tester_inputs: object = None) -> dict:
    """Redact this case's raw flows into its evidence record, then delete the
    raw file. ``{"error", "content": {"flow_count", "flows", "truncated_by_count"}}``.

    Armed with the tester's typed values for the DURATION of this call only,
    in THIS task -- :func:`tools.mobile_shared.scrub.armed_scope`, never
    the module-global arming the render path still uses. An evidence fault
    here can no more change a verdict than a failed log slice can; callers
    must never branch on the result beyond recording it.
    """
    with scrub.armed_scope(_typed_values(tester_inputs)):
        return _process(run_id, str(tc_id), _jsonl_path(run_id, str(tc_id)))


def finish_outside(run_id: str, *, tester_inputs: object = None) -> dict:
    """Same as :func:`finish_case`, for flows observed OUTSIDE any case's
    window. Called once, at the end of the run.
    """
    with scrub.armed_scope(_typed_values(tester_inputs)):
        return _process(run_id, OUTSIDE_KEY, _jsonl_path(run_id, OUTSIDE_KEY))

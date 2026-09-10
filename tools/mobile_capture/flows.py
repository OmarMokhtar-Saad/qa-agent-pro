"""Per-case flows: read the addon's raw JSONL, redact at WRITE time, cap, and
attach to the run's evidence (T5.3, T5.4).

**Routing.** The addon (``addon.py``, running INSIDE mitmdump, a different
interpreter) appends one raw line per flow to whichever file its CURRENT-CASE
pointer names, read fresh per flow. :func:`mark_case_current` /
:func:`mark_case_finished` are the ONE producer of that pointer, so a case
boundary this module has not yet announced can never be inferred by the
addon on its own. A flow observed before the first case, between two cases,
or after the last one lands in the run's OUTSIDE file rather than being
silently attributed to whichever case happens to be nearest -- the failure
the pcap lane's clock-window discipline already avoids
(``tools.mobile_evidence.capture.CLOCK_NOT_READ`` / ``clock_offset_ms``).

**Redaction happens HERE, at write time, never in the addon.** The pattern
set is exactly ``tools.mobile_evidence.scrub``'s: :data:`scrub.SECRET_HEADERS`,
:data:`scrub.SENSITIVE_KEY_RE`, :data:`scrub.MIN_SENSITIVE_LEN`, and the
tester's own typed values, armed for the duration of ONE case's redaction
through :func:`scrub.armed_scope` -- the per-TASK isolation that replaced the
old module-global arming (see ``tools/mobile_evidence/scrub.py``'s module
docstring and ``tests/mobile_evidence/test_scrub_isolation.py``). No pattern
is duplicated: this module never reimplements a mask, it only calls scrub's.

The raw JSONL is DELETED once it has been redacted into the run's evidence
directory -- on every exit, including a redaction failure. It never lives
under ``runs/``; only the redacted document does.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from tools.mobile import run_store
from tools.mobile_capture import paths
from tools.mobile_evidence import scrub

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

#: ONE body inlined into a single-file HTML report a tester opens.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_BODY_BYTES = 65536

#: ALL bodies for one run in that same single file -- the batch bound the
#: per-flow cap above cannot give.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_TOTAL_BODY_BYTES = 4 * 1024 * 1024

#: Raw bytes this module will read from ONE jsonl file. Measured at the LAST
#: consumer: the addon's own stop is best-effort in a process this server
#: does not control, so THIS is the bound that actually protects the reader.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_JSONL_BYTES = 32 * 1024 * 1024

#: One header value quoted in the report.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_HEADER_CHARS = 400

#: One URL in the endpoint table.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_URL_CHARS = 300

TRUNCATED_MARKER = " ...[truncated]"
BUDGET_OMITTED = "<omitted: total body budget for this run reached>"


def _pointer_path(run_id: str) -> Path:
    return paths.tmp_dir() / (paths.safe_name(run_id) + ".current")


def _jsonl_path(run_id: str, case_key: str) -> Path:
    return paths.tmp_dir() / (
        paths.safe_name(run_id) + "-" + paths.safe_name(case_key) + ".jsonl"
    )


def mark_case_current(run_id: str, tc_id: str) -> dict:
    """Point the addon at *tc_id*'s file. ``{"error", "content"}``, never raises."""
    try:
        target = _pointer_path(run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(paths.safe_name(tc_id), encoding="utf-8")
        os.replace(tmp, target)
        return {"error": None, "content": {"tc_id": str(tc_id)}}
    except Exception as exc:
        logger.exception("mobile_capture.flows.mark_case_current failed")
        return {"error": str(exc), "content": None}


def mark_case_finished(run_id: str) -> dict:
    """Clear the pointer, so flows from now on land in the outside bucket.

    ``{"error", "content"}``, never raises. A missing pointer is success: the
    end state -- "no case is current" -- already holds.
    """
    try:
        _pointer_path(run_id).unlink(missing_ok=True)
        return {"error": None, "content": {}}
    except Exception as exc:
        logger.exception("mobile_capture.flows.mark_case_finished failed")
        return {"error": str(exc), "content": None}


def _truncate_text(text: str, cap: int) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    return text[:cap] + TRUNCATED_MARKER, True


def _redact_headers(headers: object) -> tuple[dict, bool]:
    out: dict = {}
    truncated = False
    if isinstance(headers, dict):
        for key, value in headers.items():
            masked = scrub.redact_header(key, value)
            text, cut = _truncate_text(scrub.scrub_text(masked), MAX_HEADER_CHARS)
            truncated = truncated or cut
            out[str(key)] = text
    return out, truncated


def _redact_url(url: object) -> tuple[str, bool]:
    text = scrub.scrub_query(scrub.scrub_text(url))
    return _truncate_text(text, MAX_URL_CHARS)


def _redact_body(body: object) -> tuple[str, bool, int]:
    """Best-effort JSON redaction, falling back to text/query redaction.

    Returns ``(text, truncated, byte_len)``. A body this module cannot parse
    as JSON is not skipped -- it still goes through the query-shaped and
    free-text nets, because a form body or a plain string can carry a token
    exactly as a JSON field can.
    """
    raw = "" if body is None else str(body)
    parsed = None
    stripped = raw.strip()
    if stripped[:1] in "{[":
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            parsed = None
    if parsed is not None:
        redacted = scrub.scrub_json(parsed)
        text = json.dumps(redacted, ensure_ascii=False, sort_keys=True)
    else:
        text = scrub.scrub_query(scrub.scrub_text(raw))
    text, truncated = _truncate_text(text, MAX_BODY_BYTES)
    return text, truncated, len(text.encode("utf-8", errors="replace"))


def _redact_flow(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    url, url_cut = _redact_url(raw.get("url"))
    req_headers, req_h_cut = _redact_headers(raw.get("request_headers"))
    resp_headers, resp_h_cut = _redact_headers(raw.get("response_headers"))
    req_body, req_b_cut, req_bytes = _redact_body(raw.get("request_body"))
    resp_body, resp_b_cut, resp_bytes = _redact_body(raw.get("response_body"))
    return {
        "ts_ms": raw.get("ts_ms"),
        "method": str(raw.get("method") or ""),
        "url": url,
        "status": raw.get("status"),
        "request_headers": req_headers,
        "response_headers": resp_headers,
        "request_body": req_body,
        "response_body": resp_body,
        "truncated": bool(
            url_cut
            or req_h_cut
            or resp_h_cut
            or req_b_cut
            or resp_b_cut
            or raw.get("body_truncated")
        ),
        "_body_bytes": req_bytes + resp_bytes,
    }


def _read_raw_lines(path: Path) -> list:
    """Every parseable line of *path*, up to :data:`MAX_JSONL_BYTES` of raw
    bytes read. Malformed lines are skipped, never raised. This is the LAST
    consumer of the raw file, so this is the bound that actually protects a
    reader -- the addon's own stop is best-effort, in a process this server
    does not control.
    """
    out: list = []
    if not path.is_file():
        return out
    consumed = 0
    with open(path, "rb") as handle:
        for raw_line in handle:
            consumed += len(raw_line)
            if consumed > MAX_JSONL_BYTES:
                logger.warning(
                    "mobile_capture.flows: %s exceeds %d bytes; stopping early",
                    path,
                    MAX_JSONL_BYTES,
                )
                break
            try:
                out.append(json.loads(raw_line.decode("utf-8", errors="replace")))
            except (ValueError, TypeError):
                continue
    return out


def _process(run_id: str, key: str, path: Path) -> dict:
    """Redact one raw jsonl file into a capture-flows evidence document, then
    delete the raw file. Never raises."""
    try:
        raw_flows = _read_raw_lines(path)
        flows: list = []
        total_body_bytes = 0
        truncated_by_count = len(raw_flows) > MAX_FLOWS_PER_CASE
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
        payload = {
            "flow_count": len(flows),
            "flows": flows,
            "truncated_by_count": truncated_by_count,
        }
        write = run_store.write_evidence_json(
            run_id, None if key == OUTSIDE_KEY else key, EVIDENCE_NAME, payload
        )
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("mobile_capture.flows: could not remove raw %s", path)
        if write.get("error"):
            return {"error": write["error"], "content": None}
        return {"error": None, "content": payload}
    except Exception as exc:
        logger.exception("mobile_capture.flows._process failed for %s", key)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"error": str(exc), "content": None}


def _typed_values(tester_inputs: object) -> list:
    return list((tester_inputs or {}).values()) if isinstance(tester_inputs, dict) else []


def finish_case(run_id: str, tc_id: str, *, tester_inputs: object = None) -> dict:
    """Redact this case's raw flows into its evidence record, then delete the
    raw file. ``{"error", "content": {"flow_count", "flows", "truncated_by_count"}}``.

    Armed with the tester's typed values for the DURATION of this call only,
    in THIS task -- :func:`tools.mobile_evidence.scrub.armed_scope`, never
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

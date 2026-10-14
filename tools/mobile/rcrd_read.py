"""Reading one case record: the expected result, wall time, network record and API capture."""

from __future__ import annotations

import json
import logging

from tools.mobile import run_store
from tools.mobile.report_text import _text
from tools.mobile_capture import flows as api_flows

# same logger as the old module; kept for log filters
logger = logging.getLogger("tools.mobile.report")


def _expected_of(planned: dict) -> str:
    parts = []
    for step in planned.get("steps") or []:
        if isinstance(step, dict):
            expected = _text(step.get("expected_result"), 160)
            if expected:
                parts.append(expected)
    return " · ".join(parts)


def _case_wall(rows: list) -> int | None:
    measured = [row["ms"] for row in rows if row["ms"] is not None]
    return sum(measured) if measured else None


def _network_of(safe: dict) -> dict:
    """The case network capture record, or an empty dict. The ONE reader."""
    holder = safe.get("evidence") if isinstance(safe, dict) else None
    body = (holder or {}).get("network") if isinstance(holder, dict) else None
    return body if isinstance(body, dict) else {}


# This case's redacted API-capture flows, read from evidence/. The ONE
# reader. ``None`` when nothing was ever written for this case -- a case a
# capture never touched, distinct from one it touched and found nothing on
# (which is a document carrying an empty ``flows`` list).
#
# Never raises: an evidence fault here can no more change a verdict than a
# failed log slice can.
def _capture_of(run_id: str, tc_id: str) -> dict | None:
    """This case's redacted API-capture flows, read from evidence/; never raises."""
    if not run_id or not tc_id:
        return None
    try:
        read = run_store.read_evidence_text(run_id, tc_id, api_flows.EVIDENCE_NAME)
        text = read.get("content") if isinstance(read, dict) else None
        if not isinstance(text, str) or not text.strip():
            return None
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        logger.exception("mobile.report._capture_of failed for %s/%s", run_id, tc_id)
        return None

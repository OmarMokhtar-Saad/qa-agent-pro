"""Write-time redaction and size caps for one raw flow (see ``flows.py``).

The pattern set is exactly ``tools.mobile_shared.scrub``'s; this module never
reimplements a mask, it only calls scrub's and truncates the result.
"""

from __future__ import annotations

import json

from tools.mobile_shared import scrub

#: ONE body inlined into a single-file HTML report a tester opens.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_BODY_BYTES = 65536

#: One header value quoted in the report.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_HEADER_CHARS = 400

#: One URL in the endpoint table.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_URL_CHARS = 300

TRUNCATED_MARKER = " ...[truncated]"


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


def _parse_json_body(stripped: str) -> object:
    if stripped[:1] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return None


def redact_body(body: object) -> tuple[str, bool, int]:
    """Best-effort JSON redaction, falling back to text/query redaction.

    Returns ``(text, truncated, byte_len)``. A body this module cannot parse
    as JSON is not skipped -- it still goes through the query-shaped and
    free-text nets, because a form body or a plain string can carry a token
    exactly as a JSON field can.
    """
    raw = "" if body is None else str(body)
    parsed = _parse_json_body(raw.strip())
    if parsed is not None:
        redacted = scrub.scrub_json(parsed)
        text = json.dumps(redacted, ensure_ascii=False, sort_keys=True)
    else:
        text = scrub.scrub_query(scrub.scrub_text(raw))
    text, truncated = _truncate_text(text, MAX_BODY_BYTES)
    return text, truncated, len(text.encode("utf-8", errors="replace"))


def _flow_headers(raw: dict) -> tuple[dict, dict, bool]:
    req_headers, req_cut = _redact_headers(raw.get("request_headers"))
    resp_headers, resp_cut = _redact_headers(raw.get("response_headers"))
    return req_headers, resp_headers, req_cut or resp_cut


def _flow_bodies(raw: dict) -> tuple[str, str, bool, int]:
    req_body, req_cut, req_bytes = redact_body(raw.get("request_body"))
    resp_body, resp_cut, resp_bytes = redact_body(raw.get("response_body"))
    return req_body, resp_body, req_cut or resp_cut, req_bytes + resp_bytes


def _redact_flow(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    url, url_cut = _redact_url(raw.get("url"))
    req_headers, resp_headers, h_cut = _flow_headers(raw)
    req_body, resp_body, b_cut, body_bytes = _flow_bodies(raw)
    return {
        "ts_ms": raw.get("ts_ms"),
        "method": str(raw.get("method") or ""),
        "url": url,
        "status": raw.get("status"),
        "request_headers": req_headers,
        "response_headers": resp_headers,
        "request_body": req_body,
        "response_body": resp_body,
        "truncated": bool(url_cut or h_cut or b_cut or raw.get("body_truncated")),
        "_body_bytes": body_bytes,
    }

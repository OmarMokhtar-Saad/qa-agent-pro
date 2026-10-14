"""A data-layer call (``binding``) as an exchange-row dict."""

from __future__ import annotations

import json

from tools.mobile_evidence.exch_num import _duration
from tools.mobile_evidence.exch_shape import _path_of
from tools.mobile_shared.scrub import e

_BINDING_GAPS = {
    "requestHeaders": "not captured \u2014 the app logs this call as verb, URL and args; the headers it sent never reach the log",
    "responseHeaders": "not captured \u2014 the app logs the response body, never its headers",
}


def _binding_response_body(resp: dict) -> str:
    """The logged response of a data-layer call as text, noting what the app clipped."""
    body = str(resp.get("text") or "")
    if resp.get("json") is not None:
        body = json.dumps(resp["json"], ensure_ascii=False, indent=1)
    if resp.get("omitted"):
        body += (
            "\n\n[+"
            + str(resp["omitted"])
            + " chars clipped by the app before it was logged]"
        )
    return body


def _binding_request_body(args: object) -> str:
    """The args of a data-layer call as a request body (a dict goes to the query pane)."""
    if isinstance(args, dict):
        return ""
    if isinstance(args, list):
        return json.dumps(args, ensure_ascii=False)
    return str(args or "")


def _binding_extra(b: dict) -> str:
    """The small tags beside a data-layer call's path."""
    extra = '<span class="mt">' + e(b.get("binding") or "") + "</span>"
    if b.get("retried"):
        extra += '<span class="mt">token-refresh retry</span>'
    if b.get("forDependentSuffix"):
        extra += (
            '<span class="mt">dependent \u2026' + e(b["forDependentSuffix"]) + "</span>"
        )
    if b.get("orphanResponse"):
        extra += '<span class="mt">no request logged</span>'
    return extra


def _binding_label(b: dict) -> str | None:
    """The badge label: None when the status is known, else what the log does say."""
    if b.get("statusKnown"):
        return None
    if b.get("ok"):
        return "2xx (code not logged)"
    if b.get("ok") is None:
        return "never returned"
    return "failed (code not logged)"


# One data-layer call. The app logs a success without ever saying WHICH 2xx it was, so
# the badge is labelled rather than invented: "2xx (code not logged)", never a number the
# log did not contain.
def binding_exchange(b: dict) -> dict:
    """One data-layer call as the dict every exchange row reads."""
    url = str(b.get("url") or "")
    path = _path_of(url) or str(b.get("target") or "")
    args = b.get("args")
    return {
        "notCaptured": dict(_BINDING_GAPS),
        "method": b.get("verb") or "CALL",
        "path": path or str(b.get("binding") or ""),
        "url": url,
        "status": b.get("status") if b.get("statusKnown") else -1,
        "ok": b.get("ok"),
        "statusLabel": _binding_label(b),
        "durationMs": _duration(b),
        "query": args if isinstance(args, dict) else {},
        "requestBody": _binding_request_body(args),
        "responseBody": _binding_response_body(b.get("response") or {}),
        "extra": _binding_extra(b),
    }

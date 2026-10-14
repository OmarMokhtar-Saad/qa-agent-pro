"""A tool call as a TOOL exchange-row dict."""

from __future__ import annotations

from tools.mobile_evidence.exch_num import _duration
from tools.mobile_evidence.exch_shape import body_text

_TOOL_GAPS = {
    "requestHeaders": "not applicable \u2014 this row is a tool invocation, not an HTTP call",
    "responseHeaders": "not applicable \u2014 this row is a tool invocation, not an HTTP call",
    "query": "not applicable \u2014 a tool call has arguments, shown as the request body, not a query string",
}


def _tool_result(t: dict) -> tuple[str, str]:
    """(response text, the reason it is empty) of one tool call."""
    result = t.get("resultText")
    if result:
        return str(result), ""
    if t.get("status") == "done":
        return (
            "",
            "not captured \u2014 the app logged this tool as finished but wrote no result text with it",
        )
    return (
        "",
        "the tool never reported back \u2014 there is no completion line for this invocation in the capture",
    )


def _tool_request_body(t: dict) -> str:
    """The arguments of one tool call, flagged when they were recovered."""
    return body_text(t.get("args")) + (
        "\n\n[arguments recovered from the model's own reply: the app logs the call with empty args]"
        if t.get("argsRecovered")
        else ""
    )


def _tool_ok(t: dict) -> bool | None:
    """Whether a tool call went through: called/done, failed, or no status at all."""
    return (
        True
        if t.get("status") in ("called", "done")
        else (False if t.get("status") else None)
    )


def tool_exchange(t: dict) -> dict:
    """One tool call: what the model asked for, and what came back -- with the reason when
    nothing did."""
    kind = " \u00b7 " + str(t["kind"]) if t.get("kind") else ""
    response, gap = _tool_result(t)
    return {
        "notCaptured": {**_TOOL_GAPS, "responseBody": gap},
        "method": "TOOL",
        "path": str(t.get("tool") or "(unnamed)") + kind,
        "status": None,
        "ok": _tool_ok(t),
        "statusLabel": t.get("status") or "unknown",
        "durationMs": _duration(t),
        "requestBody": _tool_request_body(t),
        "responseBody": response,
    }

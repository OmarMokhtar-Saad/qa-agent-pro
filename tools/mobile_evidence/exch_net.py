"""A network-log line as an endpoint exchange-row dict."""

from __future__ import annotations

from tools.mobile_evidence.exch_shape import _path_of

_NET_GAPS = {
    "requestHeaders": "not captured \u2014 the app logs this call as one line: verb, URL and outcome. No header reaches the log",
    "responseHeaders": "not captured \u2014 the same line carries no response headers",
    "requestBody": "not captured \u2014 the app logs no request body on this line",
    "responseBody": "not captured \u2014 the network line carries no body; a data-layer row is where a body appears, and this call has none",
}


def _net_label(ok: bool | None) -> str:
    """The badge label of a call whose outcome is success, error or not logged."""
    return (
        "2xx (code not logged)"
        if ok
        else "failed (code not logged)"
        if ok is False
        else "outcome not logged"
    )


def _net_query(query: str) -> dict:
    """The ``a=b&c=d`` tail of a URL as a dict; blank keys are dropped."""
    q: dict = {}
    for part in query.split("&") if query else ():
        key, _, value = part.partition("=")
        if key:
            q[key] = value
    return q


# One network-log line as an endpoint row. THREE STATES, because the log has three: the
# outcome bracket is optional, so ``ok`` is None whenever the app wrote the line without
# one -- a call nobody proved succeeded is not a call that succeeded.
def network_exchange(rec: dict) -> dict:
    """One network-log line as the dict every exchange row reads."""
    url = str(rec.get("url") or "")
    path, _, query = _path_of(url).partition("?")
    outcome = str(rec.get("outcome") or "").upper()
    ok = True if outcome == "SUCCESS" else False if outcome == "ERROR" else None
    return {
        "notCaptured": dict(_NET_GAPS),
        "method": rec.get("verb") or "CALL",
        "path": path or url,
        "url": url,
        "status": -1,
        "ok": ok,
        "statusLabel": _net_label(ok),
        "query": _net_query(query),
        "requestBody": "",
        "responseBody": "",
        "extra": '<span class="mt">network log only \u2014 no data-layer row</span>',
    }

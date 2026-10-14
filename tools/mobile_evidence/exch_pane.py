"""The body panes of an exchange row and the three ways to lay a row out."""

from __future__ import annotations

from tools.mobile_evidence.exch_body import codebox
from tools.mobile_evidence.exch_head import _ex_head
from tools.mobile_evidence.exch_kv import kvtable, section
from tools.mobile_evidence.exch_seqargs import SecMeta
from tools.mobile_evidence.exch_seqk import SEQ_CUT
from tools.mobile_shared.scrub import e


def _meta(x: dict, key: str, count: int | None = None) -> SecMeta:
    """The count chip and the why-it-is-empty note a section takes from ``notCaptured``."""
    return SecMeta(count, (x.get("notCaptured") or {}).get(key, ""))


def _query_section(x: dict) -> str:
    """The Query section of the Request pane."""
    query = x.get("query") or {}
    is_map = isinstance(query, dict)
    return section(
        "Query",
        kvtable(query) if is_map else None,
        _meta(x, "query", len(query) if is_map else None),
    )


def _req_pane(x: dict) -> str:
    """The Request pane: query, headers and body, each saying why when it is empty."""
    rq_h = x.get("requestHeaders") or {}
    return (
        '<div class="exbody"><div class="pane"><div class="phead"><span class="dot req"></span>Request</div>'
        + _query_section(x)
        + section(
            "Headers",
            kvtable(rq_h, mark=True),
            _meta(x, "requestHeaders", len(rq_h)),
        )
        + section(
            "Body",
            codebox(x.get("requestBody") or ""),
            _meta(x, "requestBody"),
        )
    )


def _res_pane(x: dict, tone: tuple[str, str]) -> str:
    """The Response pane; ``tone`` is the (badge class, badge text) of the call."""
    scls, shown = tone
    rs_h = x.get("responseHeaders") or {}
    return (
        '</div><div class="pane"><div class="phead"><span class="dot res"></span>Response <span class="badge '
        + scls
        + ' sm">'
        + e(shown)
        + "</span></div>"
        + section("Headers", kvtable(rs_h), _meta(x, "responseHeaders", len(rs_h)))
        + section(
            "Body",
            codebox(x.get("responseBody") or ""),
            _meta(x, "responseBody"),
        )
        + "</div></div>"
    )


def _ex_bits(x: dict) -> tuple[str, str]:
    """(one-line head, body panes) for one call."""
    head, scls, shown = _ex_head(x)
    query = x.get("query") or {}
    rq_h = x.get("requestHeaders") or {}
    rq_b = x.get("requestBody") or ""
    rs_h = x.get("responseHeaders") or {}
    rs_b = x.get("responseBody") or ""
    inside = str(x.get("inside") or "")
    if not (query or rq_h or str(rq_b).strip() or rs_h or str(rs_b).strip() or inside):
        return head, ""
    return head, _req_pane(x) + _res_pane(x, (scls, shown)) + inside


def exchange_row(x: dict) -> str:
    """One call, both sides, collapsed to a single scannable line. No disclosure control when
    there is nothing behind it."""
    head, body = _ex_bits(x)
    if not body:
        return (
            '<div class="ex flat"><div class="exhead"><span class="chev off" aria-hidden="true"></span>'
            + head
            + '<span class="mt">nothing captured</span></div></div>'
        )
    return (
        '<details class="ex"><summary><span class="chev" aria-hidden="true"></span>'
        + head
        + "</summary>"
        + body
        + "</details>"
    )


def seq_exchange(x: dict) -> str:
    """The same call as ONE sequence row: the line is the summary, the panes the fold."""
    head, body = _ex_bits(x)
    if not body:
        return head + '<span class="mt">nothing captured</span>'
    return head + SEQ_CUT + body


def exlist(rows: list) -> str:
    return '<div class="exlist">' + "".join(rows) + "</div>"

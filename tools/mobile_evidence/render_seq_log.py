"""Sequence items drawn from a turn's run log and its flow states."""

from __future__ import annotations

from tools.mobile_evidence import exchanges
from tools.mobile_evidence.render_esc import ev_esc, guard
from tools.mobile_evidence.render_seq_util import seq_key

_FLOW_SKIP = ("seq", "turnId", "appRunId", "tool", "note", "ts", "derivedMs")


def _drawn(turn: dict) -> set:
    return {
        (str(b.get("verb") or "").upper(), str(b.get("url") or "").split("?")[0])
        for b in turn.get("bindings") or []
    }


def _net_item(rec: dict, key: float) -> tuple:
    html = guard(exchanges.seq_exchange(exchanges.network_exchange(rec)))
    return (key, exchanges.SEQ_RANK["net"], "api", html)


def _log_item(rec: dict, key: float) -> tuple:
    lane = str(rec.get("lane") or "log")
    parts = exchanges.LogParts(
        body=str(rec.get("body") or ""), bad=bool(rec.get("bad"))
    )
    html = exchanges.seq_log(lane, str(rec.get("head") or ""), parts)
    return (key, exchanges.SEQ_RANK["log"], lane, guard(html))


def runlog_items(turn: dict) -> list:
    items: list = []
    drawn = _drawn(turn)
    for rec in turn.get("runlog") or []:
        key = seq_key(rec)
        if key is None:
            continue
        if rec.get("lane") == "net" and rec.get("url"):
            seen = ((rec.get("verb") or "").upper(), str(rec.get("url")).split("?")[0])
            if seen in drawn:
                continue
            items.append(_net_item(rec, key))
        else:
            items.append(_log_item(rec, key))
    return items


def _flow_body(rec: dict) -> str:
    if not rec.get("note"):
        return ""
    return '<p class="seqfull" dir="auto">' + ev_esc(rec.get("note"), 400) + "</p>"


def _flow_item(rec: dict, key: float) -> tuple:
    bits = " · ".join(
        str(k) + "=" + str(v) for k, v in rec.items() if k not in _FLOW_SKIP
    )
    head = "flow " + str(rec.get("tool") or "") + ": " + bits
    html = guard(exchanges.seq_flow(head, exchanges.FlowParts(body=_flow_body(rec))))
    return (key, exchanges.SEQ_RANK["flow"], "flow", html)


def flow_items(turn: dict) -> list:
    items: list = []
    for rec in turn.get("flowStates") or []:
        key = seq_key(rec)
        if key is not None:
            items.append(_flow_item(rec, key))
    return items

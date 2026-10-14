"""The fall-through stream: failures and the run log of lines no matcher claimed."""

from __future__ import annotations

from tools.mobile_evidence.grammar_pat import _Grammar, _match, _names, _pat
from tools.mobile_evidence.grammar_state import (
    _bump,
    _error_entry,
    _Line,
    _ParseState,
    _stamp,
)


def runlog_lane(head: str, profile, compiled) -> str:
    """Which lane a narrated line belongs in. ``log`` is the lane of last resort."""
    lanes = getattr(profile, "runlog_lanes", None) or []
    for entry in lanes:
        try:
            lane, heads = entry[0], entry[1]
        except (TypeError, IndexError, KeyError):
            continue
        for h in _names(heads):
            if h and head.startswith(h):
                return str(lane)
    data_line = _pat(compiled, "data_line")
    if data_line is not None and data_line.match(head):
        return "net"
    return "log"


def _on_failure(state: _ParseState, line: _Line, gram: _Grammar) -> None:
    """Record an ``agent_fail`` match, else an error-level line, as an error."""
    m = _match(gram.compiled, "agent_fail", line.msg)
    if m:
        detail = {"message": (m.groupdict().get("msg") or "").strip()}
        state.errors.append(_error_entry(line, "agent", detail))
        _bump(state, line, "errors")
    elif line.ev.get("level") in ("E", "ERROR"):
        detail = {"category": line.cat, "message": line.msg}
        state.errors.append(_error_entry(line, "log", detail))
        _bump(state, line, "errors")


def _net_fields(gram: _Grammar, head: str) -> dict:
    """The verb, url and outcome of a network line (all None when it is not one)."""
    net = _match(gram.compiled, "net", head)
    ng = net.groupdict() if net else {}
    return {"verb": ng.get("verb"), "url": ng.get("url"), "outcome": ng.get("outcome")}


def _runlog_entry(line: _Line, gram: _Grammar) -> dict | None:
    """The run-log record of a line no matcher claimed, or None for a continuation or no head."""
    if line.ev.get("cont"):
        return None
    head, _sep, rest = line.msg.partition("\n")
    head = gram.head_re.sub("", head, count=1).strip()
    if not head:
        return None
    net = _net_fields(gram, head)
    return {
        **_stamp(line),
        "lane": runlog_lane(head, gram.profile, gram.compiled),
        "head": head,
        "body": rest.strip() or None,
        **net,
        "bad": any(b in head for b in gram.bad_marks),
        "category": line.cat,
        "level": line.ev.get("level"),
    }

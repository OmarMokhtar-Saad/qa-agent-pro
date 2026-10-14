"""The sequence primitives for a tool call, a flow, a capability and a narrated log line."""

from __future__ import annotations

from tools.mobile_evidence.exch_seqargs import (
    CapParts,
    FlowParts,
    LogParts,
    ToolParts,
)
from tools.mobile_evidence.exch_seqk import SEQ_CUT
from tools.mobile_shared.scrub import e


def seq_tool(name: str, parts: ToolParts | None = None) -> str:
    parts = parts or ToolParts()
    args, note, body = parts.args, parts.note, parts.body
    return (
        '<span class="seqk">tool</span><span class="tlname">'
        + e(name)
        + "</span>"
        + ('<span class="mt">' + e(note) + "</span>" if note else "")
        + args
        + ((SEQ_CUT + body) if body else "")
    )


def seq_flow(head: str, parts: FlowParts | None = None) -> str:
    parts = parts or FlowParts()
    pill_html = parts.pill_html
    detail = (parts.bits or "") + (parts.body or "")
    return (
        '<span class="seqk">flow</span><span class="seqtx" dir="auto">'
        + e(head)
        + "</span>"
        + pill_html
        + ((SEQ_CUT + detail) if detail else "")
    )


def seq_cap(cid: str, parts: CapParts | None = None) -> str:
    parts = parts or CapParts()
    text, prefill = parts.text, parts.prefill
    seed = (
        '<span class="inseed" dir="auto">' + e(prefill) + "</span>" if prefill else ""
    )
    desc = '<span class="capdesc" dir="auto">' + e(text) + "</span>" if text else ""
    detail = desc + seed
    return (
        '<span class="seqk">capability</span><span class="capid">'
        + e(cid)
        + "</span>"
        + ('<span class="tn">started</span>' if parts.started else "")
        + parts.pill_html
        + ((SEQ_CUT + detail) if detail else "")
    )


def _log_lead(lane: str, bad: bool, head: str) -> str:
    """The lane tag and the head line of a narrated log row."""
    return (
        '<span class="seqk">'
        + e(lane)
        + '</span><span class="seqtx mono'
        + (" bad" if bad else "")
        + '" dir="auto">'
        + e(head)
        + "</span>"
    )


def _log_repeat(times: int) -> str:
    """The repeat count, drawn only past one."""
    return (
        '<span class="seqx">&times;' + str(int(times)) + "</span>" if times > 1 else ""
    )


def _log_more(body: str) -> str:
    """The fold holding the raw body, if any."""
    return (SEQ_CUT + '<pre dir="auto">' + e(body) + "</pre>") if body else ""


def seq_log(lane: str, head: str, parts: LogParts | None = None) -> str:
    """A line the app narrated that no dedicated row shape claims. Deliberately UNPARSED."""
    parts = parts or LogParts()
    return (
        _log_lead(lane, parts.bad, head)
        + _log_repeat(parts.times)
        + parts.pill_html
        + _log_more(parts.body)
    )

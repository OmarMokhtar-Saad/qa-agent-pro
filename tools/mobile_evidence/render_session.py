"""The app outside every case: session entry calls and ambiguous turns."""

from __future__ import annotations

from typing import Any

from tools.mobile_evidence import evidence
from tools.mobile_evidence.render_block import SecHead, sec_block
from tools.mobile_evidence.render_esc import MAX_ROWS, clip, ev_esc, status_label
from tools.mobile_evidence.render_html import check, empty, pill
from tools.mobile_evidence.render_view import content_of, has_evidence


def _entry_row(b: dict) -> str:
    verb = str(b.get("verb") or "CALL")
    url = str(b.get("url") or b.get("target") or "")
    label = str(b.get("binding") or "")
    ok = b.get("ok")
    cls = "p-ok" if ok else ("p-void" if ok is None else "p-def")
    return check(pill(cls, status_label(b)), label or verb, verb + " " + url)


def _meta_bit(label: str, value: str) -> str:
    return (
        '<div class="mb"><span class="ml">'
        + label
        + '</span><span class="mv">'
        + value
        + "</span></div>"
    )


def _session_meta(rec: dict) -> str:
    window = (
        ev_esc(evidence.stamp(rec.get("from")), 40)
        + " &rarr; "
        + ev_esc(evidence.stamp(rec.get("to")), 40)
    )
    return (
        '<div class="metabits">'
        + _meta_bit("window", window)
        + _meta_bit("turns served", str(int(rec.get("turns") or 0)))
        + _meta_bit("model calls", str(int(rec.get("llm") or 0)))
        + _meta_bit("endpoint calls", str(int(rec.get("bindings") or 0)))
        + "</div>"
    )


def _session_run_block(rec: dict) -> str:
    entry = rec.get("entry") or []
    failed = rec.get("failed") or []
    rows = "".join(_entry_row(b) for b in entry[:MAX_ROWS])
    body = (
        ('<ul class="checks">' + rows + "</ul>")
        if rows
        else empty("no call outside a case in this app run")
    )
    note = str(len(entry)) + " entry call(s), " + str(len(failed)) + " failed"
    return sec_block(
        "App run " + str(rec.get("appRunId") or "?"),
        _session_meta(rec) + "<h4>Session entry</h4>" + body,
        SecHead(total=len(entry), note=note),
    )


def _ambiguous_row(rec: dict) -> str:
    stream = str(rec.get("stream") or "")
    what = (
        rec.get("binding")
        or rec.get("model")
        or rec.get("tool")
        or rec.get("text")
        or rec.get("head")
        or stream
    )
    return check(pill("p-gap", stream), clip(what, 80), evidence.AMBIGUOUS_NOTE)


def _ambiguous_block(amb: list) -> str:
    rows = "".join(_ambiguous_row(rec) for rec in amb[:MAX_ROWS])
    return sec_block(
        "Between two cases",
        '<ul class="checks">' + rows + "</ul>",
        SecHead(
            total=len(amb),
            note="attributed to neither card — the device clock was not read for a neighbouring case",
        ),
    )


def _session_body(ev: Any) -> tuple[str, int]:
    body = ""
    total = 0
    for rec in ev.session():
        total += len(rec.get("entry") or [])
        body += _session_run_block(rec)
    amb = ev.ambiguous_records()
    if amb:
        body += _ambiguous_block(amb)
    return body, total


def session_block(loaded: object) -> str:
    """The app outside every case: start-up, sign-in, between-case work, ambiguous turns."""
    content = content_of(loaded)
    if not has_evidence(loaded):
        return ""
    body, total = _session_body(content["evidence"])
    if not body:
        body = empty("every recorded event fell inside a case window")
    note = "start-up, sign-in and between-case work — the events that belong to no case card"
    return sec_block(
        "The app outside every case", body, SecHead(total=total or None, note=note)
    )

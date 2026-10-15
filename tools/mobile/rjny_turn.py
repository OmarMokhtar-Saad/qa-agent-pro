"""One turn of the journey: its header, steps and technical detail."""

from __future__ import annotations

from typing import Any

from tools.mobile.rcrd_verdict import _crash_block
from tools.mobile.report_media import _clip_html, _media_key
from tools.mobile.report_text import esc, fmt_ms
from tools.mobile.report_trace import _step_html, _StepSlot
from tools.mobile.report_verdict import DONE_VERDICTS, _app_logged, _unify_verdict
from tools.mobile.report_widgets import _PageData
from tools.mobile.rjny_args import _TurnIn
from tools.mobile.rjny_words import _count, _finding_of, _result_pill
from tools.mobile_evidence import render as ev_render


def _turn_tech_rows(f: dict) -> str:
    """The ``dt``/``dd`` pairs of a turn's technical detail; empty values drop."""
    tech = [
        ("Case", f["tc_id"]),
        ("Status", f["status"] or "(none)"),
        ("Why it ended", f["reason"]),
    ]
    return "".join(
        "<dt>" + esc(k, 40) + '</dt><dd dir="auto">' + esc(v, 400) + "</dd>"
        for k, v in tech
        if v
    )


def _turn_extra(f: dict, loaded: dict | None) -> str:
    """Crash, log, network and capture blocks that close a turn's detail."""
    return (
        _crash_block(f.get("crash"))
        + (ev_render.turns_table(loaded, f["tc_id"]) if _app_logged(loaded) else "")
        + ev_render.case_network(f.get("network"))
        + ev_render.case_capture(f.get("capture"))
    )


def _turn_clips(f: dict, media_map: Any) -> str:
    """The recordings stored for a turn's case."""
    return "".join(
        _clip_html(rec)
        for rec in ((media_map or {}).get("clips") or {}).get(_media_key(f["tc_id"]))
        or []
    )


def _turn_meta(f: dict) -> str:
    """``"3 steps · 1.2 s"``: the step count and, when known, the wall time."""
    meta = [_count(len(f["rows"]), "step")]
    if f["wall"] is not None:
        meta.append(fmt_ms(f["wall"]) + " on device")
    return " · ".join(meta)


def _turn_pill(verdict: str) -> str:
    """The result pill of a finished turn, or ``""`` while it is unfinished."""
    return _result_pill(_unify_verdict(verdict)) if verdict in DONE_VERDICTS else ""


def _turn_data(f: dict) -> str:
    """The case id, verdict and crash kind a turn carries as data attributes."""
    return (
        '" data-tc="'
        + esc(f["tc_id"], 40)
        + '" data-verdict="'
        + esc(f["verdict"], 24)
        + '" data-crash="'
        + esc(str((f.get("crash") or {}).get("kind") or ""), 40)
    )


def _turn_head(turn: _TurnIn, f: dict) -> str:
    """The turn's opening tag and header, left open for the rest of the article."""
    return (
        '<article class="turn" id="turn-'
        + f["slug"]
        + _turn_data(f)
        + '"><header class="turnhead"><span class="turnn">Turn '
        + str(int(turn.n))
        + '</span><h4 dir="auto">'
        + esc(_finding_of(f, turn.manifest, turn.n), 200)
        + "</h4>"
        + _turn_pill(f["verdict"])
        + '<span class="turnmeta">'
        + esc(_turn_meta(f), 60)
        + "</span></header>"
    )


def _turn_steps(f: dict, page: _PageData) -> str:
    """The turn's replayed steps, or the note that none was replayed."""
    steps = "".join(
        _step_html(_StepSlot(f["slug"], i), row, page)
        for i, row in enumerate(f["rows"])
    )
    return (
        ('<ol class="steplist">' + steps + "</ol>")
        if steps
        else '<p class="picnote">No step was replayed in this turn.</p>'
    )


def _turn_tech(f: dict, loaded: dict | None) -> str:
    """The turn's technical-detail fold."""
    return (
        '<details class="tech"><summary>Technical detail</summary><dl class="techlist">'
        + _turn_tech_rows(f)
        + "</dl>"
        + _turn_extra(f, loaded)
        + "</details>"
    )


def _turn_html(turn: _TurnIn, f: dict, page: _PageData) -> str:
    return (
        _turn_head(turn, f)
        + _turn_clips(f, page.media_map)
        + _turn_steps(f, page)
        + _turn_tech(f, page.loaded)
        + "</article>"
    )

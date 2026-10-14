"""Diagnostics parts: the collapsed part wrapper, step timings, evidence and the network block."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile import step_timing, timings_view
from tools.mobile.rdiag_perf import _perf
from tools.mobile.report_text import esc
from tools.mobile.report_widgets import _Run
from tools.mobile_evidence import render as ev_render


class _DiagHead(NamedTuple):
    """The summary line of one Diagnostics part: its title and optional note."""

    title: str
    note: str = ""


HEAD_CAPTURE = _DiagHead("Screen capture", "what the element reader kept and dropped")
HEAD_IMAGES = _DiagHead("Image processing")
HEAD_EVIDENCE = _DiagHead("App evidence", "what the app's own capture holds")
HEAD_NETWORK = _DiagHead("Network", "the app's own calls")
HEAD_LOGS = _DiagHead("Logs and findings")
HEAD_A11Y = _DiagHead("Accessibility")
HEAD_ENV = _DiagHead("Environment", "what this run ran on")


def _diag_part(sid: str, head: _DiagHead, body: str) -> str:
    """One collapsed Diagnostics part, or ``""`` when it has nothing to say."""
    if not body:
        return ""
    return (
        '<details class="diag" id="'
        + sid
        + '"><summary>'
        + esc(head.title, 60)
        + (
            ('<span class="diagnote">' + esc(head.note, 120) + "</span>")
            if head.note
            else ""
        )
        + '</summary><div class="diagbody">'
        + body
        + "</div></details>"
    )


def _timings_part(manifest: object) -> str:
    """The Step timings Diagnostics part, or ``""`` when none were recorded.

    Reads ``manifest["step_timings"]``, bounded to the newest
    ``step_timing.MAX_STEP_TIMING_ROWS`` rows; the dropped count is stated."""
    raw = (manifest if isinstance(manifest, dict) else {}).get("step_timings")
    kept, dropped = step_timing.bound_rows(raw if isinstance(raw, list) else [])
    summary = timings_view.summarize(kept)
    body = timings_view.html_section(summary)
    if not body:
        return ""
    if dropped:
        body += "<p>" + esc(str(dropped) + " older steps are not shown.", 80) + "</p>"
    head = _DiagHead("Step timings", timings_view.summary_line(summary))
    return _diag_part("step-timings", head, body)


def _has_network(facts: list, loaded: dict | None) -> bool:
    for f in facts:
        try:
            if int((f.get("network") or {}).get("packets") or 0) > 0:
                return True
        except (TypeError, ValueError, OverflowError):
            pass
        if (f.get("capture") or {}).get("flows"):
            return True
    # The loader answers {"error", "content"}; a content whose source is
    # "none" read nothing (no capture, the flag off, no profile).
    content = loaded.get("content") if isinstance(loaded, dict) else None
    return isinstance(content, dict) and content.get("source") not in (None, "", "none")


def _evidence_html(loaded: dict | None) -> str:
    """The app's own capture, summarised: its tiles, the session it came from,
    and the trust checks -- including why nothing was read (the flag is off,
    the load hit its byte cap). The old overview carried these.
    """
    tiles = "".join(ev_render.overview_tiles(loaded)) if loaded else ""
    return (
        (('<div class="kpis run">' + tiles + "</div>") if tiles else "")
        + (ev_render.session_block(loaded) + ev_render.trust_block(loaded))
        if loaded
        else ""
    )


def _network_html(run: _Run) -> str:
    """The network, API and capture sections plus the perf block, when any apply."""
    return (
        ev_render.apis_section(run.loaded)
        + ev_render.network_section(run.cases)
        + ev_render.capture_section(run.cases, run.manifest)
        + _perf(run.facts, run.loaded)
        if _has_network(run.facts, run.loaded)
        else ""
    )

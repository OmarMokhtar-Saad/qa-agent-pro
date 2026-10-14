from __future__ import annotations

import logging

from tools.mobile import knowledge_report
from tools.mobile.report_text import _text, esc
from tools.mobile.report_verdict import EXPLORATORY, _case_type, verdict_coverage
from tools.mobile.report_widgets import _Head, _Run, _sechead
from tools.mobile.rpt_ids import END_ID, KNOWLEDGE_ID
from tools.mobile_evidence import profiles as ev_profiles
from tools.mobile_evidence import render as ev_render

logger = logging.getLogger("tools.mobile.report")


def _coverage_of(coverage: object, cases: list, manifest: dict) -> dict:
    """Defaulted rather than required, so a caller that has not computed it still
    gets THE producer's answer and never a locally invented one.
    """
    return coverage if isinstance(coverage, dict) else verdict_coverage(cases, manifest)


def _load_evidence(run_id: str, manifest: dict, cases: list) -> dict | None:
    """The app's side of the run (plan P3): read ONCE, joined to the cases, and
    handed to every section. Never raises; a run without a capture, or a
    package without a profile, renders every evidence fragment as a stated gap.
    """
    profile = ev_profiles.profile_for(manifest.get("package"))
    return ev_render.load_run_evidence(ev_render.Run(run_id, manifest, cases, profile))


def _split_cases(run: _Run) -> tuple:
    """``(kinds, turns, scripted)``: the turn facts by ``tc_id`` and the rest."""
    kinds = _case_kinds(run.cases, run.manifest)
    facts_by_id = {f["tc_id"]: f for f in run.facts}
    turns = {
        tc: facts_by_id[tc]
        for tc, kind in kinds.items()
        if kind == "turn" and tc in facts_by_id
    }
    scripted = [f for f in run.facts if kinds.get(f["tc_id"]) != "turn"]
    return kinds, turns, scripted


def _case_kinds(cases: list, manifest: dict) -> dict:
    """``tc_id`` -> ``"turn"`` or ``"case"``.

    A run is goals, turns and steps. An exploratory case is a TURN, drawn in
    the journey of the goal it drove at; every other case is a scripted case,
    drawn in the case list. One map says which, so no case is drawn twice.

    `findings` is built by `_logs_section` and only there: building it twice
    is how this page once emitted id="findings" twice -- see
    test_no_id_is_emitted_twice_on_the_rendered_page.
    """
    return {
        _text(c.get("tc_id"), 40): (
            "turn" if _case_type(c, manifest) == EXPLORATORY else "case"
        )
        for c in cases
        if isinstance(c, dict)
    }


def _nav_and_sections(present: list, run_id: str, n_cases: int) -> tuple:
    """The nav links and the section markup of the parts that were emitted."""
    nav = "".join(
        '<a href="#' + sid + '" data-nav="' + sid + '">' + esc(title, 40) + "</a>"
        for sid, title, _label, _body in present
    )
    sections = (
        "".join(
            _sechead(sid, _Head(title, label), body)
            for sid, title, label, body in present
        )
        + _knowledge_html(run_id)
        + '<div id="'
        + END_ID
        + '" data-cards="'
        + str(n_cases)
        + '"></div>'
    )
    return nav, sections


def _knowledge_html(run_id: str) -> str:
    """The per-app knowledge section, ``""`` when nothing was learned. Never raises."""
    try:
        body = knowledge_report.render_section(
            knowledge_report.knowledge_section(run_id)
        )
        if not body:
            return ""
        return _sechead(
            KNOWLEDGE_ID, _Head("App knowledge", "what this run learned"), body
        )
    except Exception:
        logger.exception("knowledge section failed")
        return ""

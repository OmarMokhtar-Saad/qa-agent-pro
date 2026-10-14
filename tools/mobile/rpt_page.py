from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from tools.mobile.rdiag_page import _DiagIn
from tools.mobile.report_cards import _case_facts, _cases_html
from tools.mobile.report_diagnostics import _about_html, _app_label, _diagnostics_html
from tools.mobile.report_journey import (
    _issues_html,
    _journey_html,
    _JourneyData,
    _results_html,
    _ResultsIn,
)
from tools.mobile.report_media import _media_key, _media_map
from tools.mobile.report_shell import SHELL, SHELL_STYLE, _page_style
from tools.mobile.report_shell import _fill as fill
from tools.mobile.report_text import esc
from tools.mobile.report_verdict import _derive_goals
from tools.mobile.report_widgets import _PageData, _Run
from tools.mobile.rpt_ids import ABOUT_ID, CASES_ID, DIAG_ID, ISSUES_ID, JOURNEY_ID
from tools.mobile.rpt_input import _DocInput, _merged_library
from tools.mobile.rpt_meta import (
    _build_label,
    _footer_html,
    _meta_html,
    _source_digest,
    _title_html,
)
from tools.mobile.rpt_meta import (
    _MetaIn as _Meta,
)
from tools.mobile.rpt_parts import (
    _coverage_of,
    _load_evidence,
    _nav_and_sections,
    _split_cases,
)
from tools.mobile_evidence import render as ev_render


# Fill the shell, prune its stylesheet to what the page uses, and wrap it.
#
# The slots are built INSIDE the guard, so a slot that raises still releases.
def _fill_page(make_slots: Callable[[], dict]) -> str:
    try:
        body = fill(SHELL, make_slots())
    finally:
        # The learned-value net stays armed only while the page is being built --
        # and not a moment longer when the build raises.
        ev_render.release()
    whole = "<style>" + SHELL_STYLE + "</style>"
    if whole in body:
        markup = body.replace(whole, "", 1)
        body = body.replace(whole, "<style>" + _page_style(markup) + "</style>", 1)
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>\n'
        + body
        + "\n</body></html>\n"
    )


class _Bodies(NamedTuple):
    """The four section bodies a page may carry."""

    issues: str
    journey: str
    cases_body: str
    diagnostics: str


def _present_parts(bodies: _Bodies) -> list:
    """The top-level parts that were emitted. The nav is built from these and
    only from these, so it can never offer a section this page does not have.
    """
    issues, journey, cases_body, diagnostics = bodies
    parts = [
        (ISSUES_ID, "Issues", "what failed or was blocked", issues),
        (JOURNEY_ID, "Journey", "what the explorer did, turn by turn", journey),
        (CASES_ID, "Scripted cases", "every planned case and how it ended", cases_body),
        (DIAG_ID, "Diagnostics", "technical detail, collapsed", diagnostics),
        (ABOUT_ID, "About this report", "how to read it", _about_html()),
    ]
    return [part for part in parts if part[3]]


@dataclass(frozen=True)
class _Shell:
    run: _Run
    app: str
    present: list
    partial: bool


# The shell slots but ``RESULTS``, which the caller owns.
def _page_slots(shell: _Shell) -> dict:
    run = shell.run
    nav, sections = _nav_and_sections(shell.present, run.run_id, len(run.cases))
    build = _build_label(run.manifest)
    digest = _source_digest(run.run_id, run.manifest, len(run.cases))
    return {
        "DOC_TITLE": esc(shell.app + " · mobile run " + str(run.run_id), 160),
        "BRAND": "Mobile run",
        "BRAND_SUB": "QA Agents",
        "NAV": nav,
        "EYEBROW": "Mobile run " + esc(run.run_id, 80),
        "TITLE": _title_html(shell.app, build),
        "META": _meta_html(_Meta(run.manifest, run.cases, shell.partial, run.coverage)),
        "SECTIONS": sections,
        "FOOTER_META": _footer_html(run.run_id, run.manifest),
        "SOURCE_STAMP": esc(str(run.run_id) + ":" + digest, 80),
        # Never used by this lane: nothing here is big enough to park.
        "STORES": "",
    }


def _prep(doc: _DocInput) -> tuple:
    manifest, cases, run_id = doc.manifest, doc.cases, doc.run_id
    facts = [_case_facts(case, manifest, run_id) for case in cases]
    media_map = _media_map(cases)
    library = _merged_library(doc.screens, doc.observations)
    coverage = _coverage_of(doc.coverage, cases, manifest)
    app = _app_label(manifest)
    loaded = _load_evidence(run_id, manifest, cases)
    run = _Run(run_id, manifest, cases, facts, library, loaded, coverage)
    page = _PageData(library, app, loaded, coverage, media_map)
    return run, app, page


def _cases_by_id(cases: list) -> dict:
    return {_media_key(c.get("tc_id")): c for c in cases if isinstance(c, dict)}


# `_document_body` joins TWO libraries, once. ``screens`` answers which screens
# the run visited and stays the accessibility audit's input -- one row per
# screen, not one per look at it. ``library`` adds every stored OBSERVATION and
# is what a FRAME resolves against, so a four-turn conversation draws four
# pictures instead of one. Merged rather than passed as a pair because the
# keys do not collide IN PRACTICE: `perception._screen_id` emits twelve hex
# characters and an observation id is that id plus a dash and up to twelve
# more, so no screen id has the shape of an observation key.
#
# NOT an invariant the code enforces, and the comment used to claim it was.
# `run_store._RUN_ID_RE` accepts up to 64 characters of [A-Za-z0-9._-], so a
# STORED screen id of "aabbccdd0011-000000000000" is admissible and would
# collide, with the observation silently winning this `update`. Nothing
# produces such an id today; if something ever does, the fix is two
# dictionaries rather than a longer comment. Stated because an invariant
# asserted in prose and unenforced in code is how a reader stops checking.
#
# run_id is this branch's addition (the capture reader needs it to find the
# per-case flows); media_map is main's. Both are required: dropping either
# silently disables one lane's evidence.
def _document_body(doc: _DocInput) -> str:
    run, app, page = _prep(doc)
    kinds, turns, scripted = _split_cases(run)
    groups = _derive_goals(run.cases, run.manifest)
    issues = _issues_html(run.facts, kinds)
    journey = _journey_html(
        groups, _JourneyData(turns, _cases_by_id(run.cases), run.manifest), page
    )
    cases_body = _cases_html(scripted, page, doc.observations) if scripted else ""
    diagnostics = _diagnostics_html(run, _DiagIn(doc.screens, doc.lease, doc.partial))
    present = _present_parts(_Bodies(issues, journey, cases_body, diagnostics))
    return _fill_page(
        lambda: {
            **_page_slots(_Shell(run, app, present, doc.partial)),
            "RESULTS": _results_html(
                _ResultsIn(groups, scripted, doc.tally, len(run.facts))
            ),
        }
    )

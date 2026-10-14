"""The cases section of the report: the count lede, the toolbar and every case card."""

from __future__ import annotations

from tools.mobile import run_store
from tools.mobile.rcrd_card import _card_html
from tools.mobile.rcrd_toolbar import _toolbar
from tools.mobile.report_text import esc
from tools.mobile.report_verdict import coverage_phrase
from tools.mobile.report_widgets import _PageData


# NOT re-counted here. This lede and the four surfaces around it are ONE
# number with one meaning, produced once by ``verdict_coverage``.
def _cases_lede(facts: list, coverage: object) -> str:
    """The count lede of the cases section."""
    return (
        str(len(facts))
        + " case"
        + ("" if len(facts) == 1 else "s")
        + " checkpointed — "
        + esc(coverage_phrase(coverage), 200)
        + ". Open a case for its screens, every action and the reason it "
        "ended the way it did. Press <kbd>/</kbd> to search."
    )


def _cases_body(facts: list, loaded: dict | None, cards: str) -> str:
    """The toolbar, the case list and the no-results note."""
    return (
        _toolbar(facts, loaded)
        + '\n<div class="cases" id="caselist">'
        + (
            cards
            or '\n<p class="empty-note big">No case has been checkpointed yet.</p>'
        )
        + '</div>\n    <div class="noresults" id="noresults" hidden>\n      No cases match these filters.\n'
        '      <div><button class="chip ghost" type="button" data-clear>Clear filters</button></div>\n    </div>'
    )


# The SECTION is opened by the seven-section assembly in `_document_body`; this
# builder returns its BODY. It used to return a whole `<section id="cases">`, so
# wrapping it produced TWO elements with id="cases" -- an ambiguous nav anchor and
# invalid HTML, caught by the existing
# test_a_slot_shaped_store_value_is_not_expanded, which counts that id.
#
# The count lede stays, as a paragraph inside the body: it carries
# `coverage_phrase`, which is ONE number with one producer, and dropping it on the
# way up would have made the section head the only count on the page.
def _cases_section(facts: list, page: _PageData) -> str:
    cards = "".join(_card_html(f, page) for f in facts)
    lede = _cases_lede(facts, page.coverage)
    return (
        '\n    <p class="lede">'
        + lede
        + "</p>\n"
        + _cases_body(facts, page.loaded, cards)
    )


# The cases section, plus the store's own disclosure when the run reached
# its observation limit.
#
# Derived HERE and nowhere else. A truncated evidence set that renders as a
# complete one is the failure this whole change exists to end, so the page
# says the limit was reached rather than letting a frame read as "not
# captured" for a reason nobody can see.
#
# CONDITIONAL, because this function cannot tell whether anything was
# actually dropped. It sees how many observations were KEPT, and a run
# that ends exactly at the limit lost nothing -- the earlier wording
# said "a screen was not stored" and told such a tester their evidence
# was incomplete when it was whole. Saying what FOLLOWS from the limit
# is true in both cases; claiming a loss is only true in one.
def _cases_html(facts: list, page: _PageData, observations: object) -> str:
    """The cases section, plus the store's own disclosure when the run reached its observation limit."""
    body = _cases_section(facts, page)
    kept = len(observations) if isinstance(observations, dict) else 0
    if kept >= run_store.MAX_RUN_OBSERVATIONS:
        body += (
            '<p class="hint">This run reached the store\'s limit of '
            + str(run_store.MAX_RUN_OBSERVATIONS)
            + " kept screen observations. Any screen first seen after that point "
            "is not stored, and its frame reads as not captured.</p>"
        )
    return body

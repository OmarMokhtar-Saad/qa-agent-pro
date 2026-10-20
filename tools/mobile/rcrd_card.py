"""One case card: its summary row, its body and the wrapper that builds a whole card."""

from __future__ import annotations

from dataclasses import replace

from tools.mobile.rcrd_args import _BodyParts, _HeadParts
from tools.mobile.rcrd_attrs import _card_attrs, _card_bits, _card_snippet
from tools.mobile.rcrd_facts import _case_facts
from tools.mobile.rcrd_steps import _card_steps, _card_tech
from tools.mobile.rcrd_verdict import _ended_block, _vstrip
from tools.mobile.report_text import esc
from tools.mobile.report_verdict import _tone
from tools.mobile.report_widgets import RAIL, _PageData, _pill


def _card_head_use(facts: dict, snippet: str) -> str:
    """The title, area and snippet block of a card's summary row."""
    return (
        '<span class="cuse"><span class="cuc" dir="auto">'
        + (esc(facts["title"], 120) or "(untitled case)")
        + '</span><span class="carea">'
        + esc(facts["module"], 60)
        + " · "
        + esc(facts["priority"], 24)
        + " · "
        + esc(facts["type"], 24)
        + "</span>"
        + snippet
        + "</span>"
    )


def _card_head_state(facts: dict, tone: tuple, bits: str) -> str:
    """The verdict pill, the permalink and the metrics of a card's summary row."""
    pill_cls, label = tone
    return (
        '<span class="cstate">'
        + _pill(pill_cls, label)
        + '</span>\n    <a class="plink" href="#case-'
        + esc(facts["slug"], 40)
        + '" title="Copy a link to this record">#</a>\n    <div class="smetrics">'
        + bits
        + "</div>\n  </summary>"
    )


def _card_head(facts: dict, tone: tuple, parts: _HeadParts) -> str:
    """The card's opening tag and its summary row."""
    return (
        '\n<details class="case rail-'
        + RAIL.get(facts["verdict"], "none")
        + '" id="case-'
        + esc(facts["slug"], 40)
        + '" '
        + parts.attrs
        + '>\n  <summary>\n    <span class="cid">'
        + esc(facts["tc_id"], 40)
        + "</span>\n    "
        + _card_head_use(facts, parts.snippet)
        + "\n    "
        + _card_head_state(facts, tone, parts.bits)
    )


def _card_body(facts: dict, parts: _BodyParts) -> str:
    """The card's body: expected result, strips, steps, end state, tech fold."""
    return (
        '\n  <div class="cbody"><p class="cexp"><b>Expected</b> — '
        + parts.expected
        + "</p>"
        + _vstrip(facts)
        + parts.steps
        + _ended_block(facts)
        + '<details class="tech"><summary>Technical detail</summary>'
        + parts.tech
        + "</details>"
    )


def _card_expected(facts: dict) -> str:
    """The expected result, or the statement that the suite gives none."""
    return (
        esc(facts["expected"], 600)
        if facts["expected"]
        else "the suite states no expected result for this case"
    )


def _card_html(facts: dict, page: _PageData) -> str:
    tone = _tone(facts["verdict"])[2:]
    rows = facts["rows"]
    snippet = _card_snippet(rows[0]["action"] if rows else "", facts["reason"])
    bits = _card_bits(facts, tone, page.loaded)
    attrs = _card_attrs(facts, page.loaded)
    head = _card_head(facts, tone, _HeadParts(snippet, attrs, "".join(bits)))
    parts = _BodyParts(
        _card_expected(facts),
        _card_steps(facts, page.library, page.app),
        _card_tech(facts, page.library, page.loaded),
    )
    return head + _card_body(facts, parts) + "</div>\n</details>"


def _case_card(case: object, page: _PageData, manifest: dict | None = None) -> str:
    """One case card in the shell's anatomy: expected -> verdict -> screens -> steps -> sequence -> ended at."""
    facts = _case_facts(case, manifest if isinstance(manifest, dict) else {})
    # The map is threaded here too rather than left at its default: this
    # wrapper builds a WHOLE case card, and the report's own "how to read"
    # prose promises a photograph in a case card. A default of None here would
    # make that sentence false for any caller who used this entry point.
    return _card_html(facts, replace(page, loaded=None))

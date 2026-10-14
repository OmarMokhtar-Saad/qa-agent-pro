"""The summary line, metrics and data attributes of a case card."""

from __future__ import annotations

from tools.mobile.report_text import esc, fmt_ms
from tools.mobile.report_widgets import MSEP, _metric, _pill
from tools.mobile_evidence import render as ev_render


def _card_snippet(first_action: str, reason: str) -> str:
    """The summary line: the first action, an arrow, the stated reason."""
    if not (first_action or reason):
        return ""
    return (
        '<span class="csnip">'
        + (('<q dir="auto">' + esc(first_action, 64) + "</q>") if first_action else "")
        + (
            '<span class="arr" aria-hidden="true">→</span>'
            if first_action and reason
            else ""
        )
        + (('<q class="sara" dir="auto">' + esc(reason, 64) + "</q>") if reason else "")
        + "</span>"
    )


def _card_bits(facts: dict, tone: tuple, loaded: dict | None) -> list:
    """The summary metrics: verdict pill, step, wall, turn and screen counts."""
    pill_cls, label = tone
    bits = [_pill(pill_cls, label), MSEP, _metric(len(facts["rows"]), "steps")]
    if facts["wall"] is not None:
        bits += [MSEP, _metric(fmt_ms(facts["wall"]), "wall")]
    bits += [
        MSEP,
        _metric(facts["plans"], "LLM turns"),
        _metric(facts["escapes"], "escape hatch"),
        _metric(facts["screens"], "screens"),
    ]
    if loaded:
        # The app's side of the same case: LLM / API / tool counts, tokens, cost.
        bits += ev_render.card_metrics(loaded, facts["tc_id"])
    return bits


def _card_search(facts: dict) -> str:
    """The lower-cased text the toolbar search matches a card on."""
    rows = facts["rows"]
    return " ".join(
        [
            facts["tc_id"],
            facts["title"],
            facts["module"],
            facts["priority"],
            facts["type"],
            facts["verdict"],
            facts["reason"],
        ]
        + [row["action"] for row in rows]
        + [row["detail"] for row in rows]
    ).lower()[:4000]


# The kind, so the toolbar can filter on it and
# `report_selfcheck._pin_crashes` can pin the page against the store.
# Empty means no crash: falsy values are dropped from the attributes.
def _card_data(facts: dict, loaded: dict | None) -> dict:
    """The ``data-*`` values of a card, in the page's order."""
    data = {
        "data-tc": facts["tc_id"],
        "data-verdict": facts["verdict"],
        "data-crash": str((facts.get("crash") or {}).get("kind") or ""),
        "data-module": facts["module"],
        "data-priority": facts["priority"],
        "data-type": facts["type"],
        "data-steps": str(len(facts["rows"])),
        "data-wall": str(facts["wall"]) if facts["wall"] is not None else "",
        "data-escapes": str(facts["escapes"]),
        "data-plans": str(facts["plans"]),
        "data-lat": facts["lat"],
        "data-search": _card_search(facts),
    }
    if loaded:
        data.update(ev_render.card_data(loaded, facts["tc_id"]))
    return data


def _card_attrs(facts: dict, loaded: dict | None) -> str:
    """The card's ``data-*`` attributes: what the report toolbar filters and searches on.

    Their order is the page's: ``loaded`` evidence may override a key in place or
    add one at the end, and an empty value is dropped rather than rendered.
    """
    data = _card_data(facts, loaded)
    return " ".join(
        name + '="' + esc(value, 4000) + '"'
        for name, value in data.items()
        if value not in (None, "")
    )

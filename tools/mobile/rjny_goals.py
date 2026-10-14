"""The journey: one section per goal, each with its film strip and its turns."""

from __future__ import annotations

from tools.mobile.report_media import _turn_frame
from tools.mobile.report_text import _text, esc, fmt_ms
from tools.mobile.report_verdict import _goal_result
from tools.mobile.report_widgets import _PageData
from tools.mobile.rjny_args import _GoalSlot, _JourneyData, _Thumb, _TurnIn
from tools.mobile.rjny_thumb import _thumb_html
from tools.mobile.rjny_turn import _turn_html
from tools.mobile.rjny_words import _count, _result_pill


def _goal_meta(facts: list) -> list:
    """The turn count, step count and summed wall time shown on a goal's head."""
    steps = sum(len(f["rows"]) for f in facts)
    walls = [f["wall"] for f in facts if f["wall"] is not None]
    meta = [_count(len(facts), "turn"), _count(steps, "step")]
    if walls:
        meta.append(fmt_ms(sum(walls)))
    return meta


def _goal_facts(group: dict, facts_by_id: dict) -> list:
    """The facts of the goal's cases that have a turn."""
    return [
        facts_by_id[_text(c.get("tc_id"), 40)]
        for c in group["cases"]
        if _text(c.get("tc_id"), 40) in facts_by_id
    ]


def _goal_film(slot: _GoalSlot, data: _JourneyData, page: _PageData) -> str:
    """The goal's film strip: one thumbnail per turn."""
    return "".join(
        _thumb_html(
            f,
            _Thumb(
                slot.order[f["tc_id"]],
                _turn_frame(f["tc_id"], data.cases_by_id),
                page.library,
            ),
        )
        for f in slot.facts
    )


def _goal_turns(slot: _GoalSlot, data: _JourneyData, page: _PageData) -> str:
    """The goal's turns, in order."""
    return "".join(
        _turn_html(_TurnIn(slot.order[f["tc_id"]], data.manifest), f, page)
        for f in slot.facts
    )


def _goal_html(slot: _GoalSlot, data: _JourneyData, page: _PageData) -> str:
    return (
        '<section class="goal" aria-labelledby="goal-'
        + str(slot.gi)
        + '"><div class="goalhead"><h3 id="goal-'
        + str(slot.gi)
        + '" dir="auto">'
        + esc(slot.group["goal"], 400)
        + "</h3>"
        + _result_pill(_goal_result(slot.group["cases"]))
        + '<span class="goalmeta">'
        + esc(" · ".join(_goal_meta(slot.facts)), 80)
        + '</span></div><ol class="film">'
        + _goal_film(slot, data, page)
        + "</ol>"
        + _goal_turns(slot, data, page)
        + "</section>"
    )


def _journey_html(groups: list, data: _JourneyData, page: _PageData) -> str:
    order = {tc: i + 1 for i, tc in enumerate(data.facts_by_id)}
    out = []
    for gi, group in enumerate(groups, 1):
        facts = _goal_facts(group, data.facts_by_id)
        if facts:
            out.append(_goal_html(_GoalSlot(gi, group, facts, order), data, page))
    return "".join(out)

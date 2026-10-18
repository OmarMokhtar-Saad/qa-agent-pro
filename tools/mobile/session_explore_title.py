"""The case title of an exploratory turn; an auto-replayed one names its flow."""

from __future__ import annotations

from tools.mobile.session_explore_ids import EXPLORE_TITLE_PREFIX, explore_turn_number


def explore_turn_title(state: object, goal: str) -> str:
    """The case title of a turn: the model's ``sub_goal`` when given, else the run goal.

    An auto-replayed turn also names the flow and its steps.
    """
    body = state if isinstance(state, dict) else {}
    turn = explore_turn_number(body)
    tag = _replay_tag(body.get("auto_replay"))
    named = " ".join(str(body.get("sub_goal") or "").split()) or goal
    return (EXPLORE_TITLE_PREFIX + str(turn) + tag + " \u2014 " + named)[:250]


def titled_state(before: object, folded: object) -> dict:
    """The pre-turn state with the ``sub_goal`` this turn SUBMITTED.

    The case title reads ``sub_goal``; the pre-turn state still holds the
    PREVIOUS turn's, so a title built from it lags one turn behind.
    """
    body = dict(before) if isinstance(before, dict) else {}
    sub_goal = folded.get("sub_goal") if isinstance(folded, dict) else None
    if sub_goal:
        body["sub_goal"] = sub_goal
    return body


def _replay_tag(replay: object) -> str:
    """`` (auto-replay `flow` steps 2-6 of 9)`` for a turn that ran a flow, else ``""``."""
    if not isinstance(replay, dict) or not replay.get("note"):
        return ""
    first, last = int(replay.get("start") or 0) + 1, int(replay.get("pos") or 0)
    return " (auto-replay `%s` steps %d-%d of %d)" % (
        str(replay.get("name") or "")[:60],
        first,
        max(first, last),
        int(replay.get("total") or 0),
    )

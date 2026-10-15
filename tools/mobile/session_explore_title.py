"""The case title of an exploratory turn; an auto-replayed one names its flow."""

from __future__ import annotations

from tools.mobile.session_explore_ids import EXPLORE_TITLE_PREFIX, explore_turn_number


def explore_turn_title(state: object, goal: str) -> str:
    """The case title of a turn; an auto-replayed turn names the flow and its steps."""
    body = state if isinstance(state, dict) else {}
    turn = explore_turn_number(body)
    tag = _replay_tag(body.get("auto_replay"))
    return (EXPLORE_TITLE_PREFIX + str(turn) + tag + " \u2014 " + goal)[:250]


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

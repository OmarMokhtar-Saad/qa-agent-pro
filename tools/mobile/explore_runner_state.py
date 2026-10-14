"""The exploratory lane's session state: a fresh state, its budget, its scope record.

Cut from ``tools.mobile.explore_runner``; the facade re-exports ``new_state`` and
``remaining`` (identity). Never imports the facade.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import charter as charter_mod
from tools.mobile.explore_runner_consts import MAX_PACKAGE_CHARS, MAX_WATCH_ITEMS
from tools.mobile.explore_runner_types import StateSpec
from tools.mobile.explore_stop import DEADLINE_S, MAX_GOAL_CHARS, MAX_TURNS, _now


def _watch_items(watch_for: Any) -> list:
    items = []
    for item in list(watch_for or [])[:MAX_WATCH_ITEMS]:
        text = " ".join(str(item or "").split())[:200]
        if text:
            items.append(text)
    return items


def _state_head(goal: object, items: list) -> dict:
    return {
        "goal": " ".join(str(goal or "").split())[:MAX_GOAL_CHARS],
        "watch_for": items,
        "turn": 0,
        # The last turn number a replay actually SPENT. ``turn`` is the highest
        # number ALLOCATED and keeps that meaning for every reader it already
        # has; this is a second value with a second name, and its one job is to
        # decide which number the next packet may re-use. See ``next_turn``.
        "committed_turn": 0,
    }


# A fresh, JSON-serialisable session state. Stored in the run manifest.
#
# *charter* is the run's TERMS (``tools/mobile/charter.py``). It lives HERE,
# inside the state, rather than in a manifest key of its own, because the
# state IS ``manifest["explore"]`` and is already the dict a turn packet is
# built from -- so a resume in another chat explores under the same terms
# with no new reader and no new writer. ``charter=None`` normalises to the
# default charter and reproduces this function's previous values exactly.
def new_state(goal: object, spec: StateSpec = StateSpec()) -> dict:
    """A fresh, JSON-serialisable session state. Stored in the run manifest."""
    started = _now(spec.now)
    terms = charter_mod.normalize(spec.charter)
    # DOWNWARD ONLY. The charter can SHORTEN a run and can never lengthen one:
    # MAX_TURNS and DEADLINE_S remain the lane's ceilings, and a charter asking
    # for more gets them. See charter.budget_for.
    steps, seconds = charter_mod.budget_for(terms, MAX_TURNS, DEADLINE_S)
    items = _watch_items(spec.watch_for)
    return {
        **_state_head(goal, items),
        "turns_budget": steps,
        "started": started,
        "deadline": started + seconds,
        "extensions_used": 0,
        "guard": bool(spec.guard),
        "charter": terms,
        "stop": "",
        "findings": [],
    }


def remaining(state: object, *, now: float | None = None) -> dict:
    body = state if isinstance(state, dict) else {}
    turns = max(
        0, int(body.get("turns_budget") or MAX_TURNS) - int(body.get("turn") or 0)
    )
    seconds = max(0, int(float(body.get("deadline") or 0) - _now(now)))
    return {"turns": turns, "seconds": seconds}


def _off_charter(body: dict, screen: dict, verdict: dict) -> None:
    # RECORDED, never silently explored. The run is NOT stopped here:
    # nothing grades a scope-driven stop because nothing implements
    # one -- follow-up. The list is bounded, because it lands in a
    # manifest that a report reads.
    # ONE producer of the record, which owns BOTH the identity rule
    # (one entry per screen_id + matched line, not one per turn the
    # model lingered) and the cap. The same screen is seen on every
    # turn until the model leaves it, so an unconditional append
    # recorded one fact many times -- measured.
    body["off_charter"] = charter_mod.record_off_charter(
        body.get("off_charter"),
        {
            "turn": int(body.get("turn") or 0) + 1,
            "screen_id": str(screen.get("screen_id") or "")[:80],
            "matched": str(verdict.get("matched") or "")[:200],
        },
    )


def _record_scope(body: dict, screen: dict) -> None:
    """Write the scope verdict, the foreground package and any off-charter record."""
    # THE SCOPE VERDICT, COMPUTED ONCE. The packet builder READS
    # ``state["scope_verdict"]`` and never re-derives it: one writer, one
    # reader, so what the model is told and what the run records cannot
    # disagree. Three-valued and POSITIVE -- see charter.scope_verdict.
    verdict = charter_mod.scope_verdict(body.get("charter"), screen)
    body["scope_verdict"] = verdict
    # THE FOREGROUND PACKAGE THE SERVER SAW, for the one reader that needs
    # it: `handover` accepts a model's `app` only when it is THIS value,
    # so the guard cannot be handed to an app that is not actually in
    # front (fix round 3, item 1).
    body["screen_package"] = str(screen.get("package") or "")[:MAX_PACKAGE_CHARS]
    if verdict.get("state") == charter_mod.SCOPE_OUT:
        _off_charter(body, screen, verdict)

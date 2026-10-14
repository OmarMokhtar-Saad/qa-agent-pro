"""Verdict goals: exploratory turns grouped by the goal they drive at."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile.rtxt_clean import _text, _token
from tools.mobile.rvrd_tone import DONE_VERDICTS, _unify_verdict

#: The literal `session._explore_case` writes as an exploratory turn's one step.
EXPLORE_GOAL_PREFIX = "Explore towards the goal: "


EXPLORATORY = "Exploratory"


def _planned_case(manifest: dict, tc_id: str) -> dict:
    """The suite's own record of the case, when the manifest carries one."""
    for entry in manifest.get("cases") or []:
        if isinstance(entry, dict) and _text(entry.get("tc_id"), 40) == tc_id:
            return entry
    return {}


def _case_type(case: object, manifest: dict) -> str:
    body = case if isinstance(case, dict) else {}
    planned = _planned_case(manifest, _text(body.get("tc_id"), 40))
    return _text(planned.get("type") or body.get("type"), 40)


def _run_kind(cases: Any, manifest: dict | None = None) -> str:
    """``exploratory``, ``scripted`` or ``mixed``; an empty run is ``scripted``."""
    manifest = manifest if isinstance(manifest, dict) else {}
    kinds = {_case_type(case, manifest) == EXPLORATORY for case in list(cases or [])}
    if kinds == {True}:
        return "exploratory"
    if True in kinds:
        return "mixed"
    return "scripted"


def _run_goals(manifest: dict) -> list:
    explore = manifest.get("explore") if isinstance(manifest, dict) else None
    explore = explore if isinstance(explore, dict) else {}
    charter = explore.get("charter")
    charter = charter if isinstance(charter, dict) else {}
    goal = _text(charter.get("goal") or explore.get("goal"), 2000)
    return [goal] if goal else []


def _goal_of(case: object, manifest: dict) -> str:
    """The goal text an exploratory turn was driving at, read from its one step."""
    body = case if isinstance(case, dict) else {}
    planned = _planned_case(manifest, _text(body.get("tc_id"), 40))
    for step in planned.get("steps") or []:
        action = _text(step.get("action") if isinstance(step, dict) else "", 2000)
        if action.startswith(EXPLORE_GOAL_PREFIX):
            return action[len(EXPLORE_GOAL_PREFIX) :].strip()
    return ""


class _Groups(NamedTuple):
    """The goal groups in first-seen order, and the same groups by goal text."""

    ordered: list
    by_goal: dict


def _no_goal_group(case: object) -> dict:
    """A group of its own for a turn naming no goal, labelled by its title."""
    body = case if isinstance(case, dict) else {}
    title = _text(body.get("title") or body.get("tc_id"), 250)
    return {"goal": title, "cases": [case]}


def _match_group(goal: str, by_goal: dict) -> dict | None:
    """The group whose goal text *goal* equals or shares a prefix with, else None."""
    # A charter goal is stored whole while a turn's copy may be the
    # truncated title-length form: match on the shared prefix.
    return next(
        (
            g
            for text, g in by_goal.items()
            if text == goal or text.startswith(goal) or goal.startswith(text)
        ),
        None,
    )


def _place(case: object, goal: str, state: _Groups) -> None:
    """File *case* under its goal's group, opening a group when none matches."""
    match = _match_group(goal, state.by_goal)
    if match is None:
        match = state.by_goal[goal] = {"goal": goal, "cases": []}
        state.ordered.append(match)
    match["cases"].append(case)


# ``[{"goal": text, "cases": [case, ...]}]``. The run's charter goal is
# listed first even before a turn names it; a turn whose own step names a
# different goal opens a group of its own; a turn naming no goal at all is
# its own group labelled by its title, never dropped. Scripted cases are not
# goals and are skipped.
def _derive_goals(cases: Any, manifest: dict) -> list:
    """Exploratory turns grouped by goal, in first-seen order."""
    manifest = manifest if isinstance(manifest, dict) else {}
    state = _Groups([], {})
    for goal in _run_goals(manifest):
        state.by_goal[goal] = {"goal": goal, "cases": []}
        state.ordered.append(state.by_goal[goal])
    for case in list(cases or []):
        if _case_type(case, manifest) != EXPLORATORY:
            continue
        goal = _goal_of(case, manifest)
        if goal:
            _place(case, goal, state)
        else:
            state.ordered.append(_no_goal_group(case))
    return [g for g in state.ordered if g["cases"]]


def _goal_result(cases: list) -> str:
    """A goal's one result: the newest turn that reached a verdict decides it.

    Exploratory turns before the committing one carry no verdict of their own
    (they are steps towards the goal), so they cannot fail it; a goal whose
    turns never reached one is ``unfinished``.
    """
    for case in reversed(list(cases or [])):
        body = case if isinstance(case, dict) else {}
        verdict = _token(body.get("verdict"))
        if verdict in DONE_VERDICTS:
            return _unify_verdict(verdict)
    return "unfinished"

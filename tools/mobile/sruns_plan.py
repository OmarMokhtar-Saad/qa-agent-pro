"""Mobile session: planning a suite or an exploratory run (cut from session_runs.py, C5 w17)."""

from __future__ import annotations

import logging

from tools.mobile import explore_runner, run_store, scheduler
from tools.mobile.session_core import LANE_EXPLORE, LANE_SUITE, mint_run_id
from tools.mobile.sruns_manifest import (
    _explore_manifest,
    _no_case_survived,
    _run_records,
    _suite_manifest,
)
from tools.mobile.sruns_types import (
    CaseSets,
    ExploreOpts,
    RunTarget,
    SuiteOpts,
)

logger = logging.getLogger("tools.mobile.session")

_GOAL_REFUSAL = (
    "Give the exploratory goal in a sentence -- what should the "
    "app be made to do? Nothing was started."
)


def _kept_of(ordered: dict) -> list:
    """The cases that survived ordering and filtering."""
    return list((ordered.get("content") or {}).get("cases") or [])


def _refusal(ordered: dict) -> dict | None:
    """The reply that ends planning early, or None when cases survived."""
    if ordered.get("error"):
        return ordered
    return None if _kept_of(ordered) else _no_case_survived(ordered)


def _suite_extra(sets: CaseSets, target: RunTarget, opts: SuiteOpts) -> dict:
    """Everything a suite run's manifest carries beyond the schedule."""
    return {
        **_suite_manifest(sets, target, opts.source),
        **_run_records(target.env),
        "avd": str(target.avd or ""),
    }


def _suite_body(planned: dict, ordered: dict, run_id: str) -> dict:
    """The suite plan reply: the schedule without the case bodies."""
    body = dict(planned.get("content") or {})
    body["run_id"] = run_id
    body["lane"] = LANE_SUITE
    body["dropped"] = (ordered.get("content") or {}).get("dropped") or []
    body.pop("cases", None)
    return {"error": None, "content": body}


def plan_suite_run(
    cases: object, target: RunTarget, opts: SuiteOpts = SuiteOpts()
) -> dict:
    """Order, persist and create a suite run. ``{"error", "content": {...}}``."""
    try:
        ordered = scheduler.order_cases(cases, opts.filters)
        if (refusal := _refusal(ordered)) is not None:
            return refusal
        kept = _kept_of(ordered)
        run_id = mint_run_id()
        extra = _suite_extra(CaseSets(cases, kept), target, opts)
        planned = scheduler.plan_run(
            run_id, kept, scheduler.PlanOpts(manifest_extra=extra)
        )
        return (
            planned if planned.get("error") else _suite_body(planned, ordered, run_id)
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.plan_suite_run failed")
        return {"error": str(exc), "content": None}


# The run's TERMS go into the state, which IS manifest["explore"], so a
# resume from another chat explores under the same charter.
# charter=None reproduces this call's previous behaviour exactly.
def _explore_state(text: str, opts: ExploreOpts) -> dict:
    """The exploratory run's whole state, charter and watch-list included."""
    spec = explore_runner.StateSpec(opts.watch_for, charter=opts.charter)
    return explore_runner.new_state(text, spec)


def _explore_extra(state: dict, target: RunTarget) -> dict:
    """Everything an exploratory run's manifest starts with."""
    return {**_explore_manifest(state, target), **_run_records(target.env)}


def _explored(run_id: str, state: dict) -> dict:
    """The exploratory plan reply."""
    return {
        "error": None,
        "content": {"run_id": run_id, "lane": LANE_EXPLORE, "explore": state},
    }


def plan_explore_run(
    goal: str, target: RunTarget, opts: ExploreOpts = ExploreOpts()
) -> dict:
    """Create an exploratory run whose whole state is one manifest dict."""
    try:
        text = " ".join(str(goal or "").split())
        if len(text) < 5:
            return {"error": _GOAL_REFUSAL, "content": None}
        run_id = mint_run_id()
        state = _explore_state(text, opts)
        created = run_store.create_run(run_id, _explore_extra(state, target))
        if created.get("error"):
            return created
        return _explored(run_id, state)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.plan_explore_run failed")
        return {"error": str(exc), "content": None}

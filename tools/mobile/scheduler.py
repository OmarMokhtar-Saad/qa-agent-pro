"""Run budget: which cases, in what order, and when to pause and ask.

Risk-first ordering is not cosmetic. A mobile run is the slowest thing this
product does -- minutes per case -- so a tester who stops after twenty cases
must have spent those twenty on the riskiest ones. ``risk_scorer.score_and_sort``
already answers that question for every other surface in this repo, so the
scheduler asks IT rather than inventing a second, divergent notion of risk.

The soft gate every ``SOFT_GATE_EVERY`` cases is a question, not a stop: the run
is checkpointed either way and resuming costs one tool call.

``failed_last_run`` is read from the store, not remembered: the whole point of a
re-run filter is that the previous run happened in a different chat, possibly
before a server restart.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import NamedTuple

from tools.mobile import run_store, sdk_locator
from tools.mobile.scheduler_filter import (  # noqa: F401
    MAX_CASES,
    FilterSpec,
    _as_list,
    _drop_reason,
    _norm,
    _priority_of,
    apply_filters,
)
from tools.risk_scorer import score_and_sort

logger = logging.getLogger(__name__)

SOFT_GATE_EVERY = 20

PRIORITIES = ("Critical", "High", "Medium", "Low")


def _ordered(body: dict, cases: list, markdown: str) -> dict:
    content = {
        "cases": cases,
        "risk_markdown": markdown,
        "dropped": body.get("dropped") or [],
        "applied": body.get("applied") or [],
    }
    return {"error": None, "content": content}


def order_cases(cases: object, filters: object = None) -> dict:
    """Filter, then risk-sort. ``{"cases", "risk_markdown", "dropped", "applied"}``."""
    try:
        filtered = apply_filters(cases, filters)
        if filtered.get("error"):
            return filtered
        body = filtered["content"] or {}
        kept = list(body.get("cases") or [])
        if not kept:
            return _ordered(body, [], "")
        sorted_cases, markdown = score_and_sort(kept)
        return _ordered(body, list(sorted_cases), str(markdown or ""))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.scheduler.order_cases failed")
        return {"error": str(exc), "content": None}


class _Base(NamedTuple):
    """What a manifest records of the plan: the run, its ORDER, the filters."""

    run_id: str
    order: list[str]
    applied: list[str]


@dataclass(frozen=True)
class PlanOpts:
    """The optional inputs of ``plan_run``: the filters and extra manifest fields."""

    filters: object = None
    manifest_extra: object = None


# The same producer the explore lane reads -- see session.py. Both callers carry
# it or the field does not exist, and an absent key reads as absent on the page
# rather than as today's setting. The AVD comes from manifest_extra, which the
# suite lane fills before this update.
def _run_manifest(base: _Base, manifest_extra: object) -> dict:
    """The manifest ``plan_run`` stores: caller extras plus the run's ORDER."""
    manifest = dict(manifest_extra if isinstance(manifest_extra, dict) else {})
    avd = manifest.get("avd")
    image = str((sdk_locator.avd_system_image(avd) or {}).get("content") or "")
    manifest.update(
        {
            "run_id": base.run_id,
            "order": base.order,
            "total": len(base.order),
            "filters": base.applied,
            "planned": time.time(),
            "system_image": image,
        }
    )
    return manifest


def _no_survivor(body: dict) -> dict:
    applied = ", ".join(body.get("applied") or []) or "none"
    return {
        "error": (
            "No case survived the filters, so no run was created. "
            "Filters applied: " + applied + "."
        ),
        "content": None,
    }


def _planned(run_id: str, order: list[str], body: dict) -> dict:
    content = {
        "run_id": run_id,
        "order": order,
        "cases": list(body.get("cases") or []),
        "risk_markdown": body.get("risk_markdown") or "",
        "dropped": body.get("dropped") or [],
    }
    return {"error": None, "content": content}


def _create(run_id: str, body: dict, manifest_extra: object) -> dict:
    kept = list(body.get("cases") or [])
    order = [str(getattr(case, "tc_id", "") or "") for case in kept]
    base = _Base(run_id, order, body.get("applied") or [])
    created = run_store.create_run(run_id, _run_manifest(base, manifest_extra))
    if created.get("error"):
        return created
    return _planned(run_id, order, body)


# The order goes in the manifest because ``run_store.resume_point`` reads it to
# answer "which case is next" -- so a resume in a fresh chat replays the same
# risk order rather than re-sorting against a re-scored suite.
def plan_run(run_id: str, cases: object, opts: PlanOpts | None = None) -> dict:
    """Create the run and write its ORDER. ``{"error", "content": {...}}``."""
    try:
        opts = opts or PlanOpts()
        ordered = order_cases(cases, opts.filters)
        if ordered.get("error"):
            return ordered
        body = ordered["content"] or {}
        if not list(body.get("cases") or []):
            return _no_survivor(body)
        return _create(run_id, body, opts.manifest_extra)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.scheduler.plan_run failed")
        return {"error": str(exc), "content": None}


def _progress_of(body: dict, order: list[str]) -> dict:
    index = int(body.get("next_index") or 0)
    finished = index >= len(order)
    done = len(body.get("done") or [])
    return {
        "tc_id": "" if finished else order[index],
        "index": index,
        "total": len(order),
        "done": done,
        "failed": list(body.get("failed") or []),
        "finished": finished,
        "gate": bool(not finished and done and done % SOFT_GATE_EVERY == 0),
    }


# ``gate`` is True on a multiple of ``SOFT_GATE_EVERY`` completed cases, and
# only when there is something left -- asking "continue?" after the last case
# is noise.
def next_case(run_id: str) -> dict:
    """``{"tc_id", "index", "total", "gate", "done", "failed", "finished"}``."""
    try:
        point = run_store.resume_point(run_id)
        if point.get("error"):
            return point
        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        order = [str(v) for v in (manifest.get("order") or [])]
        return {"error": None, "content": _progress_of(point["content"] or {}, order)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.scheduler.next_case failed")
        return {"error": str(exc), "content": None}

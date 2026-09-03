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

from tools.mobile import run_store
from tools.risk_scorer import score_and_sort

logger = logging.getLogger(__name__)

SOFT_GATE_EVERY = 20
MAX_CASES = 500

PRIORITIES = ("Critical", "High", "Medium", "Low")


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part).strip() for part in value if str(part).strip()]


def apply_filters(cases: object, filters: object = None) -> dict:
    """Filter *cases*. ``{"error", "content": {"cases", "dropped", "applied"}}``.

    ``dropped`` names each excluded case AND the filter that excluded it, so a
    tester who expected 40 cases and got 6 can see why instead of guessing.
    """
    try:
        spec = filters if isinstance(filters, dict) else {}
        items = list(cases or [])
        ids = {_norm(v) for v in _as_list(spec.get("ids"))}
        priorities = {_norm(v) for v in _as_list(spec.get("priority"))}
        categories = {_norm(v) for v in _as_list(spec.get("category"))}
        failed_run = str(spec.get("failed_last_run") or "").strip()
        failed_ids: set[str] = set()
        applied: list[str] = []
        if failed_run:
            point = (run_store.resume_point(failed_run) or {}).get("content") or {}
            failed_ids = {_norm(v) for v in (point.get("failed") or [])}
            applied.append("failed_last_run=" + failed_run)
        if ids:
            applied.append("ids")
        if priorities:
            applied.append("priority")
        if categories:
            applied.append("category")

        kept, dropped = [], []
        for case in items:
            if isinstance(case, dict):
                tc_id = _norm(case.get("tc_id"))
            else:
                tc_id = _norm(getattr(case, "tc_id", ""))
            priority = _priority_of(case)
            category = _norm(getattr(case, "category", "") or "")
            if ids and tc_id not in ids:
                dropped.append({"tc_id": tc_id, "why": "not in ids"})
                continue
            if priorities and _norm(priority) not in priorities:
                dropped.append({"tc_id": tc_id, "why": "priority " + priority})
                continue
            if categories and category not in categories:
                dropped.append({"tc_id": tc_id, "why": "category " + category})
                continue
            if failed_run and tc_id not in failed_ids:
                dropped.append({"tc_id": tc_id, "why": "did not fail in " + failed_run})
                continue
            kept.append(case)
        return {
            "error": None,
            "content": {
                "cases": kept[:MAX_CASES],
                "dropped": dropped,
                "applied": applied,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.scheduler.apply_filters failed")
        return {"error": str(exc), "content": None}


def _priority_of(case: object) -> str:
    value = getattr(case, "priority", None)
    if value is None and isinstance(case, dict):
        value = case.get("priority")
    return str(getattr(value, "value", value) or "")


def order_cases(cases: object, filters: object = None) -> dict:
    """Filter, then risk-sort. ``{"cases", "risk_markdown", "dropped", "applied"}``."""
    try:
        filtered = apply_filters(cases, filters)
        if filtered.get("error"):
            return filtered
        body = filtered["content"] or {}
        kept = list(body.get("cases") or [])
        if not kept:
            return {
                "error": None,
                "content": {
                    "cases": [],
                    "risk_markdown": "",
                    "dropped": body.get("dropped") or [],
                    "applied": body.get("applied") or [],
                },
            }
        sorted_cases, markdown = score_and_sort(kept)
        return {
            "error": None,
            "content": {
                "cases": list(sorted_cases),
                "risk_markdown": str(markdown or ""),
                "dropped": body.get("dropped") or [],
                "applied": body.get("applied") or [],
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.scheduler.order_cases failed")
        return {"error": str(exc), "content": None}


def plan_run(
    run_id: str,
    cases: object,
    filters: object = None,
    manifest_extra: object = None,
) -> dict:
    """Create the run and write its ORDER. ``{"error", "content": {...}}``.

    The order goes in the manifest because ``run_store.resume_point`` reads it
    to answer "which case is next" -- so a resume in a fresh chat replays the
    same risk order rather than re-sorting against a re-scored suite.
    """
    try:
        ordered = order_cases(cases, filters)
        if ordered.get("error"):
            return ordered
        body = ordered["content"] or {}
        kept = list(body.get("cases") or [])
        if not kept:
            return {
                "error": (
                    "No case survived the filters, so no run was created. "
                    "Filters applied: "
                    + (", ".join(body.get("applied") or []) or "none")
                    + "."
                ),
                "content": None,
            }
        order = [str(getattr(case, "tc_id", "") or "") for case in kept]
        manifest = dict(manifest_extra if isinstance(manifest_extra, dict) else {})
        manifest.update(
            {
                "run_id": run_id,
                "order": order,
                "total": len(order),
                "filters": body.get("applied") or [],
                "planned": time.time(),
            }
        )
        created = run_store.create_run(run_id, manifest)
        if created.get("error"):
            return created
        return {
            "error": None,
            "content": {
                "run_id": run_id,
                "order": order,
                "cases": kept,
                "risk_markdown": body.get("risk_markdown") or "",
                "dropped": body.get("dropped") or [],
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.scheduler.plan_run failed")
        return {"error": str(exc), "content": None}


def next_case(run_id: str) -> dict:
    """``{"tc_id", "index", "total", "gate", "done", "failed", "finished"}``.

    ``gate`` is True on a multiple of ``SOFT_GATE_EVERY`` completed cases, and
    only when there is something left -- asking "continue?" after the last case
    is noise.
    """
    try:
        point = run_store.resume_point(run_id)
        if point.get("error"):
            return point
        body = point["content"] or {}
        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        order = [str(v) for v in (manifest.get("order") or [])]
        index = int(body.get("next_index") or 0)
        finished = index >= len(order)
        done = len(body.get("done") or [])
        return {
            "error": None,
            "content": {
                "tc_id": "" if finished else order[index],
                "index": index,
                "total": len(order),
                "done": done,
                "failed": list(body.get("failed") or []),
                "finished": finished,
                "gate": bool(not finished and done and done % SOFT_GATE_EVERY == 0),
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.scheduler.next_case failed")
        return {"error": str(exc), "content": None}

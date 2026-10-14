"""Run budget, part 1: which cases survive the tester's filters.

``failed_last_run`` is read from the store, not remembered: the whole point of a
re-run filter is that the previous run happened in a different chat, possibly
before a server restart. ``tools.mobile.scheduler`` re-exports every name here.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from tools.mobile import run_store

# The facade's logger name: the split changes no log line's source.
logger = logging.getLogger("tools.mobile.scheduler")

MAX_CASES = 500


class FilterSpec(NamedTuple):
    """The parsed filters: normalised id/priority/category sets and a re-run."""

    ids: set[str]
    priorities: set[str]
    categories: set[str]
    failed_run: str
    failed_ids: set[str]


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part).strip() for part in value if str(part).strip()]


def _priority_of(case: object) -> str:
    value = getattr(case, "priority", None)
    if value is None and isinstance(case, dict):
        value = case.get("priority")
    return str(getattr(value, "value", value) or "")


def _drop_reason(case: object, spec: FilterSpec) -> tuple[str, str | None]:
    """``(tc_id, why)``: *why* names the first filter excluding *case*, else None."""
    if isinstance(case, dict):
        tc_id = _norm(case.get("tc_id"))
    else:
        tc_id = _norm(getattr(case, "tc_id", ""))
    priority = _priority_of(case)
    category = _norm(getattr(case, "category", "") or "")
    if spec.ids and tc_id not in spec.ids:
        return tc_id, "not in ids"
    if spec.priorities and _norm(priority) not in spec.priorities:
        return tc_id, "priority " + priority
    if spec.categories and category not in spec.categories:
        return tc_id, "category " + category
    if spec.failed_run and tc_id not in spec.failed_ids:
        return tc_id, "did not fail in " + spec.failed_run
    return tc_id, None


def _norm_set(value: Any) -> set[str]:
    return {_norm(v) for v in _as_list(value)}


def _failed_ids(failed_run: str) -> set[str]:
    if not failed_run:
        return set()
    point = (run_store.resume_point(failed_run) or {}).get("content") or {}
    return {_norm(v) for v in (point.get("failed") or [])}


def _applied(spec: FilterSpec) -> list[str]:
    named = (
        ("ids", spec.ids),
        ("priority", spec.priorities),
        ("category", spec.categories),
    )
    head = ["failed_last_run=" + spec.failed_run] if spec.failed_run else []
    return head + [name for name, active in named if active]


def _parse(filters: object) -> tuple[FilterSpec, list[str]]:
    raw = filters if isinstance(filters, dict) else {}
    failed_run = str(raw.get("failed_last_run") or "").strip()
    spec = FilterSpec(
        _norm_set(raw.get("ids")),
        _norm_set(raw.get("priority")),
        _norm_set(raw.get("category")),
        failed_run,
        _failed_ids(failed_run),
    )
    return spec, _applied(spec)


def _partition(items: list, spec: FilterSpec) -> tuple[list, list]:
    kept, dropped = [], []
    for case in items:
        tc_id, why = _drop_reason(case, spec)
        if why is None:
            kept.append(case)
        else:
            dropped.append({"tc_id": tc_id, "why": why})
    return kept, dropped


def apply_filters(cases: Any, filters: object = None) -> dict:
    """Filter *cases*. ``{"error", "content": {"cases", "dropped", "applied"}}``.

    ``dropped`` names each excluded case AND the filter that excluded it, so a
    tester who expected 40 cases and got 6 can see why instead of guessing.
    """
    try:
        spec, applied = _parse(filters)
        kept, dropped = _partition(list(cases or []), spec)
        content = {"cases": kept[:MAX_CASES], "dropped": dropped, "applied": applied}
        return {"error": None, "content": content}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.scheduler.apply_filters failed")
        return {"error": str(exc), "content": None}

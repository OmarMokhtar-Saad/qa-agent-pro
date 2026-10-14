"""A case's view and the counters read back from its checkpoint."""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import run_store
from tools.mobile.case_run_consts import (
    REASON_BUDGET,
    REASON_SELECTOR,
    REASON_VISUAL,
)

# Same object as case_run_consts.logger (getLogger is a registry); a local
# binding lets ruff see the handler as logged.
logger = logging.getLogger("tools.mobile.case_runner")


def _case_body(case: Any) -> dict:
    """The case as a plain dict: the model dump, or the dict it is shaped like."""
    if hasattr(case, "model_dump"):
        return case.model_dump(mode="json")
    return dict(case or {})


def _steps_view(body: dict) -> list:
    """The case's steps, each cut to the fields and sizes a packet needs."""
    steps = []
    for step in body.get("steps") or []:
        if not isinstance(step, dict):
            continue
        steps.append(
            {
                "step_number": int(step.get("step_number") or 0),
                "action": str(step.get("action") or "")[:600],
                "test_data": str(step.get("test_data") or "")[:300],
                "expected_result": str(step.get("expected_result") or "")[:600],
            }
        )
    return steps


# case_view: a ``TestCase`` (or a dict shaped like one) reduced to what a packet
# needs.
#
# Deliberately NOT the model dump: risk fields, stable ids and automation
# status say nothing to a planner and every byte competes with the screen.
def case_view(case: Any) -> dict:
    """A ``TestCase`` (or a dict shaped like one) reduced to what a packet needs."""
    try:
        body = _case_body(case)
        steps = _steps_view(body)
        return {
            "tc_id": str(body.get("tc_id") or ""),
            "title": str(body.get("title") or "")[:250],
            "module": str(body.get("module") or "")[:100],
            "priority": str(body.get("priority") or ""),
            "type": str(body.get("type") or ""),
            "preconditions": str(body.get("preconditions") or "")[:600],
            "steps": steps,
        }
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.case_runner.case_view failed")
        return {"tc_id": "", "title": "", "steps": []}


def _prior_body(prior: object) -> tuple[dict, dict]:
    """``(body, merged)``: the checkpoint body and its merged uncharged dict."""
    body = prior if isinstance(prior, dict) else {}
    merged = body.get("uncharged_stops")
    return body, merged if isinstance(merged, dict) else {}


# _prior_uncharged: a checkpoint's uncharged-stop counts, by reason, never
# raising.
#
# Guarded because the carry-forward used to do a bare ``int()`` on whatever
# was on disk, so a corrupt count broke every later checkpoint for that case.
# Reads the merged key first, then the two pre-merge flat keys -- and reads
# the legacy one when the merged value is UNREADABLE as well as when it is
# missing. ``merged.get(reason, body.get(legacy))`` only did the second, so a
# corrupt merged value discarded a good legacy one: measured,
# ``{"uncharged_stops": {"budget": "x"}, "budget_stops": 3}`` returned
# ``budget: 0``, which hands that one case a full cap of extra uncharged
# stops. Not a loop -- the next checkpoint writes a clean merged dict -- and
# not laxer than the pre-merge behaviour, but wrong.
def _prior_uncharged(prior: object) -> dict:
    """A checkpoint's uncharged-stop counts, by reason, never raising."""
    body, merged = _prior_body(prior)
    out = {}
    for reason, legacy in (
        (REASON_BUDGET, "budget_stops"),
        (REASON_SELECTOR, "free_stops"),
        (REASON_VISUAL, ""),
    ):
        value = _uncharged_count(merged.get(reason))
        if value is None:
            value = _uncharged_count(body.get(legacy))
        out[reason] = 0 if value is None else value
    return out


def _uncharged_count(raw: Any) -> int | None:
    """One stored count, coerced, or ``None`` when it cannot be read.

    ``None`` rather than ``0`` for the failure, because the caller has to tell
    "unreadable, try the other key" from "the stored count really is zero" --
    conflating the two is the defect this helper exists to remove.

    ``""`` counts as unreadable: a blank string on disk is corruption, not a
    zero. ``OverflowError`` is caught alongside the other two because
    ``int(float("inf"))`` raises it (``int(float("nan"))`` raises ValueError),
    and this function's whole contract is that it never raises into a
    checkpoint.
    """
    try:
        if raw is None or raw == "":
            return None
        return max(0, int(raw))
    except (TypeError, ValueError, OverflowError):
        return None


def uncharged_stops(run_id: str, tc_id: str) -> dict:
    """Every uncharged stop this case has spent, by reason, read from disk.

    On disk for the same reason ``escapes`` is: the MCP server restarts on every
    code and ``.env`` edit, and an in-memory counter would mean "free forever,
    one restart at a time".

    Reads the merged ``uncharged_stops`` dict, and falls back to the two flat
    keys a checkpoint written by either branch before the merge would carry --
    so a run started on either side is still counted correctly.
    """
    body = (run_store.read_case(run_id, tc_id) or {}).get("content")
    body = body if isinstance(body, dict) else {}
    return _prior_uncharged(body)


def budget_stops_used(run_id: str, tc_id: str) -> int:
    """How many times the submit budget has already ended this case, from disk.

    On the checkpoint for the same reason the escape count is: the MCP server
    restarts on every code and `.env` edit, and a counter held in memory would
    mean "loop forever, one restart at a time".
    """
    body = (run_store.read_case(run_id, tc_id) or {}).get("content")
    if not isinstance(body, dict):
        return 0
    try:
        return uncharged_stops(run_id, tc_id)[REASON_BUDGET]
    except (TypeError, ValueError):
        return 0


def free_stops_used(run_id: str, tc_id: str) -> int:
    """Uncharged stale-selector stops this case has already spent, from disk.

    On disk for the same reason ``escapes`` is: the MCP server restarts on every
    code and ``.env`` edit, and an in-memory counter would mean "free forever,
    one restart at a time".
    """
    body = (run_store.read_case(run_id, tc_id) or {}).get("content")
    if not isinstance(body, dict):
        return 0
    try:
        return uncharged_stops(run_id, tc_id)[REASON_SELECTOR]
    except (TypeError, ValueError):
        return 0


def escapes_used(run_id: str, tc_id: str) -> int:
    """How many times this case has already boomeranged, read from disk."""
    body = (run_store.read_case(run_id, tc_id) or {}).get("content")
    if not isinstance(body, dict):
        return 0
    try:
        return max(0, int(body.get("escapes") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0

"""The token totals of a parsed capture."""

from __future__ import annotations


def _fresh_totals() -> dict:
    """The empty token totals, in the report's key order."""
    return {
        "in": 0,
        "out": 0,
        "total": 0,
        "unattributed": 0,
        "byModel": {},
        "callsWithUsage": 0,
        "callsWithoutUsage": 0,
    }


def _add_usage(agg: dict, call: dict, usage: dict) -> None:
    """Add one llm call's usage to the totals and to its model's row."""
    agg["callsWithUsage"] += 1
    for key in ("in", "out", "total", "unattributed"):
        if usage.get(key):
            agg[key] += usage[key]
    model = usage.get("model") or call.get("model") or "unknown"
    per = agg["byModel"].setdefault(model, {"calls": 0, "in": 0, "out": 0, "total": 0})
    per["calls"] += 1
    for key in ("in", "out", "total"):
        if usage.get(key):
            per[key] += usage[key]


def totals(parsed):
    agg = _fresh_totals()
    for call in parsed.get("llm") or []:
        usage = call.get("tokens")
        if not usage:
            agg["callsWithoutUsage"] += 1
            continue
        _add_usage(agg, call, usage)
    return agg

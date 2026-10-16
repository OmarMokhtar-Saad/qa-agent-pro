"""What a saved flow remembers beyond its steps: the screen before and after each one.

``meta = {before: [sk2 ...], after: [sk2 ...], reached: bool}``, one entry per step. It
lets the server match a flow to the screen in front and check each step landed. Screen
keys are structure hashes, never text. Never raises.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import flow_stats

MAX_KEY_CHARS = 64
MAX_GOAL_CHARS = 120


def _keys(raw: Any, count: int) -> list:
    got = [str(k or "")[:MAX_KEY_CHARS] for k in raw] if isinstance(raw, list) else []
    return (got + [""] * count)[:count]


def clean(meta: Any, count: int) -> dict:
    """*meta* shaped to *count* steps, or ``{}`` when it holds nothing usable."""
    if not isinstance(meta, dict) or count < 1:
        return {}
    before = _keys(meta.get("before"), count)
    if not any(before):
        return {}
    out = {
        "before": before,
        "after": _keys(meta.get("after"), count),
        "reached": meta.get("reached") is True,
    }
    return {**out, **_extras(meta)}


def _extras(meta: dict) -> dict:
    """The optional record: the goal a flow reached and its replay counters."""
    goal = " ".join(str(meta.get("goal") or "").split())[:MAX_GOAL_CHARS]
    stats = flow_stats.clean(meta.get("stats"))
    return {**({"goal": goal} if goal else {}), **({"stats": stats} if stats else {})}


def reached(template: dict) -> bool:
    meta = template.get("meta") if isinstance(template, dict) else None
    return isinstance(meta, dict) and meta.get("reached") is True


def _label(step: dict) -> str:
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    named = target.get("text") or target.get("label") or step.get("text") or ""
    named = "" if str(named).startswith("{") else str(named)[:30]
    return ("%s %s" % (step.get("op"), named)).strip()


def covers(template: dict) -> str:
    """One line on what the flow spans: first and last step, and whether it got to the goal."""
    steps = list(template.get("steps") or [])
    if not steps:
        return "no steps"
    end = "reached the goal" if reached(template) else "stops short of the goal"
    return "starts with `%s`, ends with `%s`, %s" % (
        _label(steps[0]),
        _label(steps[-1]),
        end,
    )

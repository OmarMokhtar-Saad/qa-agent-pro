"""Auto-replay: which saved flow fits the screen in front, and what each step needs.

Pure reads of the app's saved flows. A flow matches when the screen key (sk2) of one
of its steps equals the live screen's: any step on a fresh run (the flow resumes
there), or the step a stopped replay was waiting at. Never raises.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import app_flows, screen_key
from tools.mobile.app_flows_consts import PLACEHOLDER_RE

logger = logging.getLogger(__name__)


def live_key(screen: Any, ctx: Any) -> str:
    """The sk2 key of *screen* as the replay would stamp it, or ``""``."""
    try:
        package = str(getattr(ctx, "package", "") or "")
        activity = str(getattr(ctx, "activity", "") or "")
        return screen_key.screen_key(screen, package, activity).key
    except Exception:
        logger.exception("flow replay: screen key failed")
        return ""


def _templates(package: str) -> list:
    rows = (app_flows.list_flows(package).get("content")) or []
    out = []
    for row in rows:
        got = app_flows.get_flow(package, row["name"])
        if not got.get("error"):
            out.append((row["name"], got["content"]))
    return out


def template(package: str, name: str) -> dict:
    """The saved flow *name*, or ``{}``."""
    got = app_flows.get_flow(package, name)
    return {} if got.get("error") else got["content"]


def starts(template: dict, key: str, at: int) -> bool:
    before = (template.get("meta") or {}).get("before") or []
    return at < len(before) and bool(key) and before[at] == key


def _first_at(template: dict, key: str) -> int:
    """The earliest step whose recorded screen is *key*, or ``-1``."""
    before = (template.get("meta") or {}).get("before") or []
    return before.index(key) if key and key in before else -1


def _rank(item: tuple) -> tuple:
    name, template, at = item
    reached = bool((template.get("meta") or {}).get("reached"))
    return (reached, len(template.get("steps") or []) - at, -at)


def pick(package: str, key: str, skip: Any = ()) -> tuple:
    """``(name, template, step)`` of the best flow passing *key*, else ``("", {}, 0)``.

    The flow may reach the live screen at any step, not only its first (a one-time
    first-launch screen is skipped on the next run). A flow that reaches the goal
    beats one that does not, then the longer remaining suffix, then the earlier step.
    Names in *skip* (already replayed this run) are never offered again.
    """
    skipped = set(skip)
    fits = [
        (name, template, at)
        for name, template in _templates(package)
        if name not in skipped and (at := _first_at(template, key)) >= 0
    ]
    return max(fits, key=_rank) if fits else ("", {}, 0)


def needed(step: dict) -> str:
    """The tester input a step needs (a `{param}` or a secret field), or ``""``."""
    if step.get("op") not in ("type", "fill"):
        return ""
    if step.get("secret"):
        return str(step.get("field") or "")
    found = PLACEHOLDER_RE.fullmatch(str(step.get("text") or ""))
    return found.group(1) if found else ""


def fill(step: dict, inputs: dict) -> dict:
    """*step* with its `{param}` replaced from *inputs* (a secret step is left alone)."""
    name = needed(step)
    if not name or step.get("secret"):
        return dict(step)
    return dict(step, text=str(inputs[name]))

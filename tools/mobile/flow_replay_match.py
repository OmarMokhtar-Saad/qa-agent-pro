"""Auto-replay: which saved flow fits the screen in front, and what each step needs.

Pure reads of the app's saved flows. A flow matches when the screen key (sk3; sk2 for older flows) of one
of its steps equals the live screen's: any step on a fresh run (the flow resumes
there), or the step a stopped replay was waiting at. Never raises.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import app_flows, flow_inert, flow_stats, screen_key
from tools.mobile import flow_replay_match_anchor as anchor
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


def live_keys(screen: Any, ctx: Any) -> tuple:
    """Every key *screen* may be stored under: its sk3 key, then its sk2 forms.

    Flows saved before sk3 hold sk2 keys, which hashed the screen's own activity,
    the context's (memoised, so possibly stale) one, or none; matching any of them
    keeps those flows usable.
    """
    body = screen if isinstance(screen, dict) else {}
    package = str(getattr(ctx, "package", "") or body.get("package") or "")
    stale = str(getattr(ctx, "activity", "") or "")
    own = str(body.get("activity") or "")
    keys = [live_key(screen, ctx)] + [
        _sk2_with(body, package, a) for a in (own, stale, "")
    ]
    return anchor.Keys(dict.fromkeys(k for k in keys if k), screen)


def _sk2_with(screen: dict, package: str, activity: str) -> str:
    """The sk2 key of *screen* hashed with *activity* (``""``: with none at all)."""
    try:
        return screen_key.sk2_key(screen, package, activity)
    except Exception:
        logger.exception("flow replay: sk2 screen key failed")
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


def _keys(key: Any) -> tuple:
    """*key* (one key or several) as a tuple of non-empty keys."""
    keys = (key,) if isinstance(key, str) else tuple(key or ())
    return tuple(k for k in keys if k)


def _meta(template: dict, which: str) -> list:
    return (template.get("meta") or {}).get(which) or []


def landing_keys(template: dict, at: int) -> tuple:
    """Screen keys that count as "step *at* may run here".

    ``before[at]`` and ``after[at - 1]`` name one screen (the step before ended where
    this one starts), but a transient read can stamp either, so both are accepted.
    """
    before, after = _meta(template, "before"), _meta(template, "after")
    keys = [
        before[at] if at < len(before) else "",
        after[at - 1] if 0 < at <= len(after) else "",
    ]
    return tuple(dict.fromkeys(k for k in keys if k))


def starts_on_key(template: dict, key: str, at: int) -> bool:
    """Whether step *at* may start on *key* (one key or several), by key alone."""
    if at >= len(template.get("steps") or []):
        return False
    return bool(set(_keys(key)) & set(landing_keys(template, at)))


def starts(template: dict, key: str, at: int) -> bool:
    """Whether step *at* may start on *key*, or on the live screen *key* carries."""
    return starts_on_key(template, key, at) or anchor.fits(template, key, at)


def _first_at(template: dict, key: str, by_key: bool = True) -> int:
    """The earliest step that may start on *key* (``by_key`` False: by its screen)."""
    test = starts_on_key if by_key else anchor.fits
    for at in range(len(template.get("steps") or [])):
        if test(template, key, at):
            return at
    return -1


def known_keys(package: str) -> frozenset:
    """Every screen key any saved flow of *package* recorded (never raises)."""
    try:
        rows = _templates(package)
    except Exception:
        logger.exception("flow replay: known keys failed")
        return frozenset()
    keys = [k for _n, t in rows for w in ("before", "after") for k in _meta(t, w)]
    return frozenset(k for k in keys if k)


def _rank(item: tuple) -> tuple:
    name, template, at = item
    reached = bool((template.get("meta") or {}).get("reached"))
    suffix = len(template.get("steps") or []) - at
    return (reached, flow_stats.score(template), suffix, -at)


def _usable(template: dict) -> bool:
    """Worth replaying: not dead (0 ok in its tries) and not inert (no required step)."""
    return not flow_stats.dead(template) and not flow_inert.inert(template)


def pick(package: str, key: str, skip: Any = ()) -> tuple:
    """``(name, template, step)`` of the best flow passing *key*, else ``("", {}, 0)``.

    The flow may reach the live screen at any step, not only its first (a one-time
    first-launch screen is skipped on the next run). A flow that reaches the goal
    beats one that does not, then the higher replay score (Wilson lower bound of its
    success, decayed by age), then, as a tie-break, the longer remaining suffix and
    the earlier step.
    A flow no key fits may still start where the live screen shows its own target
    (``flow_replay_match_anchor``); a key match always beats that.
    Names in *skip* (already replayed this run) are never offered again.
    """
    live = [(n, t) for n, t in _templates(package) if _usable(t)]
    rows = [(n, t) for n, t in live if n not in set(skip)]
    for by_key in (True, False):
        fits = [(n, t, at) for n, t in rows if (at := _first_at(t, key, by_key)) >= 0]
        if fits:
            return max(fits, key=_rank)
    return ("", {}, 0)


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

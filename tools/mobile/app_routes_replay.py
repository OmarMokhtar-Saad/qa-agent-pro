"""Reading a finished replay against a saved route (see ``app_routes``)."""

from __future__ import annotations

import logging
import re
from typing import Any, NamedTuple, Optional, cast

from tools.mobile.app_flows_consts import OK_OUTCOMES, err
from tools.mobile.app_flows_replay import clean_replay

logger = logging.getLogger("tools.mobile.app_routes")

_SCREEN_ID_RE = re.compile(r"[0-9a-f]{8,64}")


class RouteSpec(NamedTuple):
    """What a route remembers: the screen each step starts on, the end screen, the template."""

    screens: Any
    end: Any
    template: Optional[Any] = None


def _good_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_SCREEN_ID_RE.fullmatch(value))


def ids_ok(screens: Any, end: Any, count: int) -> bool:
    return (
        isinstance(screens, list)
        and len(screens) == count
        and all(_good_id(item) for item in screens)
        and _good_id(end)
    )


def _consecutive(tail: list) -> bool:
    """True when the entries' ``index`` values run +1 each, i.e. they are ONE submit's steps."""
    indexes = [entry.get("index") for entry in tail]
    if any(isinstance(i, bool) or not isinstance(i, int) for i in indexes):
        return False
    return all(later == earlier + 1 for earlier, later in zip(indexes, indexes[1:]))


def _tail(case: Any, count: int):
    """The last *count* trace entries when they are one clean replay of this route, else None.

    The trace is merged across submits, so an outcome check alone cannot tell a replay that was
    cut short (its earlier, clean entries are from another submit) from a finished one; the
    per-submit ``index`` run can.
    """
    if not clean_replay(case, count):
        return None
    tail = case["trace"][-count:]
    return tail if _consecutive(tail) else None


def record_from_case(case: Any, count: int) -> dict:
    """The ``{'screens', 'end'}`` to store from a clean replay of *count* steps, or an error.

    The ids are read RAW: a missing, non-string or malformed one fails ``ids_ok`` instead of
    being coerced into a string that might pass it.
    """
    try:
        tail = _tail(case, count)
        if tail is None:
            return err("the replay did not finish cleanly, one step after another")
        screens = [entry.get("before_screen_id") for entry in tail]
        end = tail[-1].get("after_screen_id")
        if not ids_ok(screens, end, count):
            return err("a step did not record the screen it started or ended on")
        return {"error": None, "content": {"screens": screens, "end": end}}
    except Exception:
        logger.exception("mobile.app_routes.record_from_case failed")
        return err("the replay could not be read")


def replay_matched(case: Any, count: int, spec: RouteSpec) -> bool:
    """True when the replay ran clean, every step started on its saved screen and the last
    ended on the saved end screen. The executor already refuses a wrong start; this is the
    check on the END, and a second look at the starts."""
    try:
        tail = _tail(case, count)
        if tail is None or not ids_ok(spec.screens, spec.end, count):
            return False
        starts = [entry.get("before_screen_id") for entry in tail]
        return (
            starts == list(cast("list", spec.screens))
            and tail[-1].get("after_screen_id") == spec.end
        )
    except Exception:
        logger.exception("mobile.app_routes.replay_matched failed")
        return False


def stop_line(name: str, case: Any) -> str:
    """The sentence for a route replay that did not finish as saved. Never says a step was skipped."""
    trace = case.get("trace") if isinstance(case, dict) else None
    last = (
        trace[-1]
        if isinstance(trace, list) and trace and isinstance(trace[-1], dict)
        else {}
    )
    outcome = str(last.get("outcome") or "")
    if outcome == "route_mismatch":
        why = "a screen was not the one saved for that step"
    elif outcome in OK_OUTCOMES:
        why = "it did not end on the saved screen"
    else:
        why = "a step did not finish as saved"
    return (
        "Route `%s` stopped: %s. Every step it ran was checked first; carry on from the "
        "packet below, and save the route again if the app changed." % (name, why)
    )

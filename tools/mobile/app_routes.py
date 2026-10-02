"""Saved routes: a known trip an app remembers, every hop checked (``routes.json``).

PROVISIONAL (owner has not confirmed): a screen's fingerprint is its ``screen_id``.

A route is a flow TEMPLATE (``app_flows``: steps and labels, never a typed value) plus the
``screen_id`` each step must START on and the ``screen_id`` the last step must END on, both read
from the trace of the clean run that saved it. Replay is ONE script through ``session.submit``
and ``executor.replay`` with ``Context.route_expect`` set: the executor compares the live screen
with the saved one before every step and a mismatch stops with nothing tapped; the wrapper then
checks the end screen. This module adds no way to act on the device and never calls a model.
Every public function returns an error/content dict (or a plain value) and never raises.
"""

from __future__ import annotations

import copy
import json
import logging
import re

from tools.mobile import app_flows, app_store

logger = logging.getLogger(__name__)

ROUTES_FILE = "routes.json"

#: Routes one app may keep. A NEW name past it is refused; overwriting one is not.
MAX_ROUTES_PER_APP = 20

_SCREEN_ID_RE = re.compile(r"[0-9a-f]{8,64}")
#: Stands in for a screen id while the byte cap is checked BEFORE the replay produced the real ones.
_SIZE_PROBE_ID = "0" * 12


def _err(message: str) -> dict:
    return {"error": message, "content": None}


def _bad_name() -> str:
    return (
        "not a valid route name (lower-case letters, digits, _ and -, at most %d characters)"
        % (app_flows.MAX_FLOW_NAME_CHARS)
    )


def _good_id(value: object) -> bool:
    return isinstance(value, str) and bool(_SCREEN_ID_RE.fullmatch(value))


def _ids_ok(screens: object, end: object, count: int) -> bool:
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


def _tail(case: object, count: int):
    """The last *count* trace entries when they are one clean replay of this route, else None.

    The trace is merged across submits, so an outcome check alone cannot tell a replay that was
    cut short (its earlier, clean entries are from another submit) from a finished one; the
    per-submit ``index`` run can.
    """
    if not app_flows.clean_replay(case, count):
        return None
    tail = case["trace"][-count:]
    return tail if _consecutive(tail) else None


def record_from_case(case: object, count: int) -> dict:
    """The ``{'screens', 'end'}`` to store from a clean replay of *count* steps, or an error.

    The ids are read RAW: a missing, non-string or malformed one fails ``_ids_ok`` instead of
    being coerced into a string that might pass it.
    """
    try:
        tail = _tail(case, count)
        if tail is None:
            return _err("the replay did not finish cleanly, one step after another")
        screens = [entry.get("before_screen_id") for entry in tail]
        end = tail[-1].get("after_screen_id")
        if not _ids_ok(screens, end, count):
            return _err("a step did not record the screen it started or ended on")
        return {"error": None, "content": {"screens": screens, "end": end}}
    except Exception:
        logger.exception("mobile.app_routes.record_from_case failed")
        return _err("the replay could not be read")


def replay_matched(case: object, count: int, screens: object, end: object) -> bool:
    """True when the replay ran clean, every step started on its saved screen and the last
    ended on the saved end screen. The executor already refuses a wrong start; this is the
    check on the END, and a second look at the starts."""
    try:
        tail = _tail(case, count)
        if tail is None or not _ids_ok(screens, end, count):
            return False
        starts = [entry.get("before_screen_id") for entry in tail]
        return starts == list(screens) and tail[-1].get("after_screen_id") == end
    except Exception:
        logger.exception("mobile.app_routes.replay_matched failed")
        return False


def _check_entry(entry: object) -> dict:
    """A stored route re-validated: the file is user-editable, so it is never trusted."""
    if not isinstance(entry, dict):
        return _err("the saved route is damaged")
    got = app_flows.template_from_script(entry.get("steps"))
    if got.get("error"):
        return got
    template = got["content"]
    screens = entry.get("screens")
    end = entry.get("end")
    if not _ids_ok(screens, end, len(template["steps"])):
        return _err("the saved route is damaged (its screens do not match its steps)")
    return {
        "error": None,
        "content": {**template, "screens": list(screens), "end": end},
    }


def _add_route(body: dict, name: str, template: dict, screens: list, end: str) -> str:
    """Put the route into *body* under *name*; ``''`` or the count-cap refusal."""
    routes = body.get("routes")
    if not isinstance(routes, dict):
        routes = body["routes"] = {}
    if name not in routes and len(routes) >= MAX_ROUTES_PER_APP:
        return (
            "this app already has %d saved routes; delete one first"
            % MAX_ROUTES_PER_APP
        )
    routes[name] = {
        "params": list(template["params"]),
        "steps": list(template["steps"]),
        "screens": list(screens),
        "end": end,
    }
    return ""


def check_save(package: object, name: object, template: dict) -> str:
    """Why saving *template* as route *name* would be refused, or ``''``: run BEFORE the replay,
    so a route that cannot be stored never drives the device. ``save_route`` re-checks."""
    try:
        if not app_flows.valid_name(name):
            return _bad_name()
        body = copy.deepcopy(app_store.read_json(package, ROUTES_FILE))
        probe = [_SIZE_PROBE_ID] * len(template["steps"])
        problem = _add_route(body, str(name), template, probe, _SIZE_PROBE_ID)
        if problem:
            return problem
        size = len(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if size > app_store.MAX_FILE_BYTES:
            return (
                "this route would push the saved routes past %d bytes; delete one first"
                % (app_store.MAX_FILE_BYTES)
            )
        return ""
    except Exception:
        logger.exception("mobile.app_routes.check_save failed")
        return "the route store is unavailable"


def save_route(
    package: object, name: object, template: dict, screens: object, end: object
) -> dict:
    """Store the route. Refuses an empty or malformed screen id and a screen list that does not
    match the steps one for one."""
    try:
        if not app_flows.valid_name(name):
            return _err(_bad_name())
        if not _ids_ok(screens, end, len(template.get("steps") or [])):
            return _err(
                "a route needs the screen each step starts on and the screen it ends on"
            )
        return app_store.update_json(
            package,
            ROUTES_FILE,
            lambda body: _add_route(body, str(name), template, screens, end),
        )
    except Exception:
        logger.exception("mobile.app_routes.save_route failed")
        return _err("the route could not be saved")


def get_route(package: object, name: object) -> dict:
    """The stored route ``{params, steps, screens, end}``, re-validated on every load."""
    try:
        if not app_flows.valid_name(name):
            return _err(_bad_name())
        routes = app_store.read_json(package, ROUTES_FILE).get("routes")
        entry = routes.get(name) if isinstance(routes, dict) else None
        if not isinstance(entry, dict):
            return _err("no saved route with that name")
        return _check_entry(entry)
    except Exception:
        logger.exception("mobile.app_routes.get_route failed")
        return _err("the route store is unavailable")


def list_routes(package: object) -> dict:
    try:
        routes = app_store.read_json(package, ROUTES_FILE).get("routes")
        rows: list = []
        for name in sorted(routes if isinstance(routes, dict) else ()):
            got = get_route(package, name)
            if got.get("error"):
                continue
            route = got["content"]
            rows.append(
                {
                    "name": name,
                    "steps": len(route["steps"]),
                    "params": route["params"],
                    "fields": app_flows.secret_fields(route),
                }
            )
        return {"error": None, "content": rows}
    except Exception:
        logger.exception("mobile.app_routes.list_routes failed")
        return _err("the route store is unavailable")


def delete_route(package: object, name: object) -> dict:
    try:
        if not app_flows.valid_name(name):
            return _err(_bad_name())

        def mutate(body: dict) -> str:
            routes = body.get("routes")
            if isinstance(routes, dict) and name in routes:
                del routes[name]
                return ""
            return "no saved route with that name"

        return app_store.update_json(package, ROUTES_FILE, mutate)
    except Exception:
        logger.exception("mobile.app_routes.delete_route failed")
        return _err("the route could not be deleted")


def stop_line(name: str, case: object) -> str:
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
    elif outcome in app_flows.OK_OUTCOMES:
        why = "it did not end on the saved screen"
    else:
        why = "a step did not finish as saved"
    return (
        "Route `%s` stopped: %s. Every step it ran was checked first; carry on from the "
        "packet below, and save the route again if the app changed." % (name, why)
    )

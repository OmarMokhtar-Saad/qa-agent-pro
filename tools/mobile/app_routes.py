"""Saved routes: a known trip an app remembers, every hop checked (``routes.json``).

PROVISIONAL (owner has not confirmed): a screen's fingerprint is its ``screen_id``.

A route is a flow TEMPLATE (``app_flows``: steps and labels, never a typed value) plus the
``screen_id`` each step must START on and the ``screen_id`` the last step must END on, both read
from the trace of the clean run that saved it. Replay is ONE script through ``session_submit.submit``
and ``replay.replay.replay`` with ``Context.route_expect`` set: the executor compares the live screen
with the saved one before every step and a mismatch stops with nothing tapped; the wrapper then
checks the end screen. This module adds no way to act on the device and never calls a model.
Every public function returns an error/content dict (or a plain value) and never raises.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, cast

from tools.mobile import app_flows, app_store
from tools.mobile.app_flows_consts import err as _err
from tools.mobile.app_routes_replay import (
    RouteSpec,
    record_from_case,
    replay_matched,
    stop_line,
)
from tools.mobile.app_routes_replay import ids_ok as _ids_ok

__all__ = [
    "MAX_ROUTES_PER_APP",
    "ROUTES_FILE",
    "RouteSpec",
    "check_save",
    "delete_route",
    "get_route",
    "list_routes",
    "record_from_case",
    "replay_matched",
    "save_route",
    "stop_line",
]

logger = logging.getLogger(__name__)

ROUTES_FILE = "routes.json"

#: Routes one app may keep. A NEW name past it is refused; overwriting one is not.
MAX_ROUTES_PER_APP = 20

#: Stands in for a screen id while the byte cap is checked BEFORE the replay produced the real ones.
_SIZE_PROBE_ID = "0" * 12


def _bad_name() -> str:
    return (
        "not a valid route name (lower-case letters, digits, _ and -, at most %d characters)"
        % (app_flows.MAX_FLOW_NAME_CHARS)
    )


def _check_entry(entry: Any) -> dict:
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
        "content": {**template, "screens": list(cast("list", screens)), "end": end},
    }


def _add_route(body: dict, name: str, spec: RouteSpec) -> str:
    """Put the route into *body* under *name*; ``''`` or the count-cap refusal."""
    routes = body.get("routes")
    if not isinstance(routes, dict):
        routes = body["routes"] = {}
    if name not in routes and len(routes) >= MAX_ROUTES_PER_APP:
        return (
            "this app already has %d saved routes; delete one first"
            % MAX_ROUTES_PER_APP
        )
    template = cast("dict", spec.template)
    routes[name] = {
        "params": list(template["params"]),
        "steps": list(template["steps"]),
        "screens": list(cast("list", spec.screens)),
        "end": spec.end,
    }
    return ""


def _too_big(body: dict) -> str:
    size = len(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    if size > app_store.MAX_FILE_BYTES:
        return (
            "this route would push the saved routes past %d bytes; delete one first"
            % (app_store.MAX_FILE_BYTES)
        )
    return ""


def check_save(package: Any, name: Any, template: dict) -> str:
    """Why saving *template* as route *name* would be refused, or ``''``: run BEFORE the replay,
    so a route that cannot be stored never drives the device. ``save_route`` re-checks."""
    try:
        if not app_flows.valid_name(name):
            return _bad_name()
        body = copy.deepcopy(app_store.read_json(package, ROUTES_FILE))
        probe = [_SIZE_PROBE_ID] * len(template["steps"])
        spec = RouteSpec(probe, _SIZE_PROBE_ID, template)
        return _add_route(body, str(name), spec) or _too_big(body)
    except Exception:
        logger.exception("mobile.app_routes.check_save failed")
        return "the route store is unavailable"


def save_route(package: Any, name: Any, spec: RouteSpec) -> dict:
    """Store the route. Refuses an empty or malformed screen id and a screen list that does not
    match the steps one for one."""
    try:
        if not app_flows.valid_name(name):
            return _err(_bad_name())
        count = len(cast("dict", spec.template).get("steps") or [])
        if not _ids_ok(spec.screens, spec.end, count):
            return _err(
                "a route needs the screen each step starts on and the screen it ends on"
            )
        return app_store.update_json(
            package, ROUTES_FILE, lambda body: _add_route(body, str(name), spec)
        )
    except Exception:
        logger.exception("mobile.app_routes.save_route failed")
        return _err("the route could not be saved")


def get_route(package: Any, name: Any) -> dict:
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


def _route_row(name: str, route: dict) -> dict:
    return {
        "name": name,
        "steps": len(route["steps"]),
        "params": route["params"],
        "fields": app_flows.secret_fields(route),
    }


def list_routes(package: Any) -> dict:
    try:
        routes = app_store.read_json(package, ROUTES_FILE).get("routes")
        rows: list = []
        for name in sorted(routes if isinstance(routes, dict) else ()):
            got = get_route(package, name)
            if got.get("error"):
                continue
            rows.append(_route_row(name, got["content"]))
        return {"error": None, "content": rows}
    except Exception:
        logger.exception("mobile.app_routes.list_routes failed")
        return _err("the route store is unavailable")


def delete_route(package: Any, name: Any) -> dict:
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

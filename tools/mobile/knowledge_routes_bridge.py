"""Read-only bridge from saved S10 routes to learned shortcuts.

Routes stay exactly as they are (JSON, perception ``screen_id``, saved by the
tester): this module only READS them through ``app_routes`` and never writes
``routes.json``. A route becomes a shortcut CANDIDATE (``origin="route:<name>"``,
trust ``tester``) with empty start/end keys, because a perception ``screen_id``
cannot be turned into a ``screen_key`` (one-way hashes). When a later run's OK
steps replay the route's ops and rids back to back, that run supplies the keys
and the seed becomes ``active`` at once (tester trust needs no 3-run wait).
Nothing here raises.
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

from tools.mobile import app_routes, knowledge_db
from tools.mobile.kroute_activate import (  # noqa: F401
    RouteRun,
    _find_run,
    activate_seeds,
)

logger = logging.getLogger(__name__)

ORIGIN = "route:"
_OPS = ("tap", "type")
_PARAM = re.compile(r"^\{([A-Za-z0-9_.-]{1,40})\}$")


def _rid(step: dict) -> str:
    target = cast(
        "dict", step.get("target") if isinstance(step.get("target"), dict) else {}
    )
    return str(target.get("rid") or step.get("rid") or "")


def seed_steps(route: dict) -> tuple[list, dict]:
    """``(steps, args)`` of a route as shortcut steps, or ``([], {})`` when any step
    cannot be replayed by rid. Typed text is never kept: a parameter placeholder
    or a secret field name only."""
    steps: list = []
    args: dict = {}
    for raw in list((route or {}).get("steps") or []):
        op, rid = str(raw.get("op") or ""), _rid(raw)
        if op not in _OPS or not rid:
            return [], {}
        step = {"op": op, "rid": rid}
        if op == "type":
            match = _PARAM.match(str(raw.get("text") or ""))
            name = match.group(1) if match else "arg_%d" % (len(args) + 1)
            step["arg"] = name
            args[name] = {"field": str(raw.get("field") or "")[:80]}
            if raw.get("field"):
                step["field"] = str(raw["field"])[:80]
        steps.append(step)
    return steps, args


def route_steps(package: str, name: str) -> tuple[list, dict]:
    got = app_routes.get_route(package, name)
    if got.get("error"):
        return [], {}
    return seed_steps(got.get("content") or {})


def _seeded(conn: Any, name: str) -> list:
    return knowledge_db.rows(
        conn,
        "shortcuts",
        knowledge_db.Where("name = ? AND invalid_at IS NULL", ("route-" + name,), 1),
    )


def _seed_values(name: str, steps: list, args: dict) -> dict:
    return {
        "name": "route-" + name,
        "origin": ORIGIN + name,
        "trust": "tester",
        "status": "candidate",
        "steps_json": steps,
        "args_json": args,
        "precondition_json": {"screen_key": "", "rids": [steps[0]["rid"]]},
        "start_key": "",
        "end_key": "",
    }


def _insert_seed(conn: Any, values: dict, run_id: str) -> bool:
    return bool(
        knowledge_db.insert(
            conn,
            "shortcuts",
            knowledge_db.Write(values, run_id=run_id, why="route seed"),
        )
    )


def seed_shortcuts_from_routes(package: str, conn: Any, run_id: str = "") -> int:
    """Insert one candidate shortcut per saved route not seeded yet. Returns rows added."""
    added = 0
    listed = app_routes.list_routes(package)
    if listed.get("error"):
        return 0
    for item in list(listed.get("content") or []):
        name = str(item.get("name") or "")
        have = _seeded(conn, name)
        steps, args = route_steps(package, name)
        if have or not steps:
            continue
        added += _insert_seed(conn, _seed_values(name, steps, args), run_id)
    return added

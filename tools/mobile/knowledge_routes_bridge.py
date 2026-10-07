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

import json
import logging
import re

from tools.mobile import app_routes, knowledge_db

logger = logging.getLogger(__name__)

ORIGIN = "route:"
_OPS = ("tap", "type")
_PARAM = re.compile(r"^\{([A-Za-z0-9_.-]{1,40})\}$")


def _rid(step: dict) -> str:
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
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


def seed_shortcuts_from_routes(package: str, conn: object, run_id: str = "") -> int:
    """Insert one candidate shortcut per saved route not seeded yet. Returns rows added."""
    added = 0
    listed = app_routes.list_routes(package)
    if listed.get("error"):
        return 0
    for item in list(listed.get("content") or []):
        name = str(item.get("name") or "")
        have = knowledge_db.rows(
            conn, "shortcuts", "name = ? AND invalid_at IS NULL", ("route-" + name,), 1
        )
        steps, args = route_steps(package, name)
        if have or not steps:
            continue
        values = {
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
        if knowledge_db.insert(conn, "shortcuts", values, run_id=run_id, why="route seed"):
            added += 1
    return added


def _find_run(recs: list, steps: list) -> tuple[str, str]:
    """``(start_key, end_key)`` of the first back-to-back OK run of *recs* that has
    the route's ops and rids in order, else ``("", "")``."""
    want = [(s["op"], s["rid"]) for s in steps]
    for i in range(len(recs) - len(want) + 1):
        window = recs[i : i + len(want)]
        if [(r.get("op"), r.get("target_rid")) for r in window] != want:
            continue
        if any(r.get("outcome") != "ok" for r in window):
            continue
        if any(a.get("after_screen_key") != b.get("screen_key") for a, b in zip(window, window[1:], strict=False)):
            continue
        return str(window[0]["screen_key"]), str(window[-1].get("after_screen_key") or "")
    return "", ""


def activate_seeds(conn: object, package: str, recs: list, run_id: str = "") -> int:
    """Activate seeds whose route this run replayed. Returns seeds activated."""
    done = 0
    pending = knowledge_db.rows(
        conn, "shortcuts", "origin LIKE 'route:%' AND status = 'candidate' AND start_key = '' AND invalid_at IS NULL", (), 50
    )
    for row in pending:
        try:
            steps = json.loads(row.get("steps_json") or "[]")
        except ValueError:
            continue
        start, end = _find_run(recs, steps) if steps else ("", "")
        if not start or not end or start.startswith("sk1d:"):
            continue
        pre = {"screen_key": start, "rids": [steps[0]["rid"]]}
        if knowledge_db.set_status(
            conn, "shortcuts", row["id"], "active", run_id=run_id, why="route replayed",
            start_key=start, end_key=end, precondition_json=json.dumps(pre),
        ):
            done += 1
    return done

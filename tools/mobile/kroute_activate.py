"""Activate route-seeded shortcuts once a later run replays the route back to back."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from tools.mobile import knowledge_db


class RouteRun(NamedTuple):
    """One run's package, step records and run id, as ``activate_seeds`` reads them."""

    package: str
    recs: list
    run_id: str = ""


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
        if any(
            a.get("after_screen_key") != b.get("screen_key")
            for a, b in zip(window, window[1:], strict=False)
        ):
            continue
        return str(window[0]["screen_key"]), str(
            window[-1].get("after_screen_key") or ""
        )
    return "", ""


def _steps_of(row: dict) -> list:
    """The row's replayable steps, or ``[]`` when the stored JSON is not a step list."""
    try:
        steps = json.loads(row.get("steps_json") or "[]")
    except ValueError:
        return []
    ok = isinstance(steps, list) and all(
        isinstance(s, dict)
        and isinstance(s.get("op"), str)
        and isinstance(s.get("rid"), str)
        for s in steps
    )
    return steps if ok else []


def _found(row: dict, run: RouteRun) -> tuple[str, str]:
    """``(start, end)`` keys of the run that replayed *row*'s route, else ``("", "")``."""
    steps = _steps_of(row)
    start, end = _find_run(run.recs, steps) if steps else ("", "")
    return ("", "") if start.startswith("sk1d:") else (start, end)


def _active_fields(row: dict, start: str, end: str) -> dict:
    pre = {"screen_key": start, "rids": [_steps_of(row)[0]["rid"]]}
    return {"start_key": start, "end_key": end, "precondition_json": json.dumps(pre)}


def _activate_row(conn: Any, row: dict, run: RouteRun) -> bool:
    start, end = _found(row, run)
    if not start or not end:
        return False
    status = knowledge_db.Status(
        row["id"],
        "active",
        _active_fields(row, start, end),
        run_id=run.run_id,
        why="route replayed",
    )
    return bool(knowledge_db.set_status(conn, "shortcuts", status))


def activate_seeds(conn: Any, run: RouteRun) -> int:
    """Activate seeds whose route this run replayed. Returns seeds activated."""
    done = 0
    pending = knowledge_db.rows(
        conn,
        "shortcuts",
        knowledge_db.Where(
            "origin LIKE 'route:%' AND status = 'candidate' AND start_key = '' AND invalid_at IS NULL",
            (),
            50,
        ),
    )
    for row in pending:
        done += _activate_row(conn, row, run)
    return done

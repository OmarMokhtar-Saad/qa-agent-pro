"""Popup row writes (split out of knowledge_stage_popups)."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from tools.mobile import knowledge_db


class Sighting(NamedTuple):
    run_id: str
    popup: tuple
    sig: str


def _stored_rid(row: dict) -> Any:
    try:
        got = json.loads(row.get("dismiss_json") or "{}")
    except ValueError:
        return None
    return got.get("rid") if isinstance(got, dict) else None


def squatting(found: list, rid: str) -> bool:
    return (
        bool(found)
        and found[0].get("trust") == "imported"
        and _stored_rid(found[0]) != rid
    )


def credit(conn, row: dict, s: Sighting) -> int:
    """Count one more sighting on an existing row, once per run."""
    try:
        runs = json.loads(row.get("runs_json") or "[]")
    except ValueError:
        runs = []
    if s.run_id and s.run_id in runs:
        return 0
    counter = knowledge_db.Counter(
        {"signature": s.sig},
        {"times_seen": s.popup[3], "runs_seen": 1},
        run_id=s.run_id,
        why="popup seen",
    )
    return 1 if knowledge_db.upsert_counter(conn, "popups", counter) else 0


def _values(s: Sighting) -> dict:
    key, activity, rid, count = s.popup
    dismiss = {"op": "tap", "rid": rid, "screen_key": key, "activity": activity}
    return {
        "signature": s.sig,
        "dismiss_json": dismiss,
        "times_seen": count,
        "runs_seen": 1,
        "status": "candidate",
    }


def _replace(conn, found: list, s: Sighting) -> int:
    # An import never earns local credit for a dismiss this install did not
    # observe: a fresh local row takes its place.
    replace = knowledge_db.Replace(
        found[0]["id"],
        _values(s),
        run_id=s.run_id,
        why="imported popup, other dismiss",
    )
    return 1 if knowledge_db.supersede(conn, "popups", replace) else 0


def _candidate(conn, s: Sighting) -> int:
    write = knowledge_db.Write(_values(s), run_id=s.run_id, why="popup candidate")
    return 1 if knowledge_db.insert(conn, "popups", write) else 0


def store(conn, s: Sighting, found: list) -> int:
    squat = squatting(found, s.popup[2])
    if found and not squat:
        return credit(conn, found[0], s)
    if squat:
        return _replace(conn, found, s)
    return _candidate(conn, s)

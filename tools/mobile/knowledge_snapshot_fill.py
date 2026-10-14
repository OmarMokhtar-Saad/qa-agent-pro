from __future__ import annotations

import json

from tools.mobile import app_knowledge
from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits

_LIVE = ("active", "needs_recheck")


def _env_ok(row: dict, env: object) -> bool:
    try:
        when = json.loads(row.get("when_json") or "{}")
    except ValueError:
        return False
    return app_knowledge.env_matches((when or {}).get("env"), env)


def _place(snap: dict, row: dict, table: str) -> None:
    tagged = dict(row, _table=table)
    bucket = row.get("screen_key") or ""
    if bucket:
        snap["lessons"].setdefault(bucket, []).append(tagged)
    else:
        snap["global"].append(tagged)


def _fill_notes(snap: dict, notes: list, env: object) -> None:
    for row in sorted(notes, key=lambda r: r["id"]):
        if row.get("proposal_json") or row["status"] == "disputed":
            snap["disputed"].append(dict(row, _table="notes"))
        elif row["status"] in _LIVE and _env_ok(row, env):
            _place(snap, row, "notes")


def _fill_lessons(snap: dict, lessons: list) -> None:
    for row in sorted(lessons, key=lambda r: r["id"]):
        _place(snap, row, "lessons")


def fill(conn: object, facts: object, snap: dict) -> None:
    env = getattr(facts, "env", None) or {}
    cap = limits.SNAPSHOT_MAX_ROWS
    notes_where = (
        "status IN ('active', 'needs_recheck', 'disputed') AND invalid_at IS NULL"
    )
    notes = kdb.rows(conn, "notes", kdb.Where(notes_where, (), cap))
    lessons_where = "status = 'active' AND invalid_at IS NULL"
    lessons = kdb.rows(conn, "lessons", kdb.Where(lessons_where, (), cap))
    _fill_notes(snap, notes, env)
    _fill_lessons(snap, lessons)

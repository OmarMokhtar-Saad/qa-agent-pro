"""Mistake row reads and the credit upsert (split out of knowledge_stage_mistakes)."""

from __future__ import annotations

import json
from typing import NamedTuple

from tools.mobile import knowledge_db
from tools.mobile.kstg_mist_sig import texts


class Lookup(NamedTuple):
    table: str
    where: str
    args: tuple


class Credit(NamedTuple):
    hit: dict
    run_id: str


def find_open(conn, q: Lookup):
    where = knowledge_db.Where(q.where + " AND invalid_at IS NULL", q.args, 1)
    got = knowledge_db.rows(conn, q.table, where)
    return got[0] if got else None


def runs_of(row: dict) -> list:
    try:
        got = json.loads(str(row.get("runs_json") or "[]"))
        return got if isinstance(got, list) else []
    except ValueError:
        return []


def distinct(row: dict) -> int:
    return len(set(runs_of(row)))


def _key_of(hit: dict) -> dict:
    return {
        "screen_key": hit["rec"]["screen_key"],
        "element_fp": str(hit["rec"].get("target_rid") or ""),
        "bad_pattern": hit["sig"],
    }


def _new_row(conn, hit: dict, run_id: str) -> int:
    symptom, fix = texts(hit)
    extra = {}
    if hit["sig"] == "tester_correction":
        extra = {"trust": "tester", "status": "active"}
    values = {
        **_key_of(hit),
        "symptom": symptom,
        "fix": fix,
        "count": 1,
        "runs_seen": 1,
        **extra,
    }
    write = knowledge_db.Write(values, run_id=run_id, why="mistake")
    return int(knowledge_db.insert(conn, "mistakes", write) is not None)


def _again(conn, old: dict, c: Credit) -> int:
    key = _key_of(c.hit)
    tester = c.hit["sig"] == "tester_correction"
    deltas = {"count": 1, "runs_seen": 0 if c.run_id in runs_of(old) else 1}
    counter = knowledge_db.Counter(key, deltas, run_id=c.run_id, why="mistake")
    row_id = knowledge_db.upsert_counter(conn, "mistakes", counter)
    if row_id is not None and tester and old["status"] != "active":
        status = knowledge_db.Status(
            row_id, "active", {"trust": "tester"}, run_id=c.run_id, why="mistake tester"
        )
        knowledge_db.set_status(conn, "mistakes", status)
    return int(row_id is not None)


def credit(conn, hit: dict, run_id: str) -> int:
    """Upsert one mistake row; the number of rows written (0 or 1)."""
    key = _key_of(hit)
    q = Lookup(
        "mistakes",
        "screen_key = ? AND element_fp = ? AND bad_pattern = ?",
        tuple(key.values()),
    )
    old = find_open(conn, q)
    if old is None:
        return _new_row(conn, hit, run_id)
    return _again(conn, old, Credit(hit, run_id))

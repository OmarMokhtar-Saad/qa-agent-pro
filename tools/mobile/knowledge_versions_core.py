"""Shared parts of the version bookkeeping: tables, parameter objects and row helpers."""

from __future__ import annotations

import json
from typing import Any, NamedTuple, cast

FLAG_TABLES = ("elements", "shortcuts", "mistakes", "popups", "notes")
ACTIVE = "status = 'active' AND invalid_at IS NULL"
_FLAG_LIKE = ("recheck@%", "missing@%", "remap@%")


class VerCtx(NamedTuple):
    version: str
    run_id: str


class Scope(NamedTuple):
    ctx: VerCtx
    cache: dict


class Flagged(NamedTuple):
    table: str
    row: dict


class Gone(NamedTuple):
    key: str
    rid: str
    version: str
    run_id: str


class Remap(NamedTuple):
    old_key: str
    new_key: str


def loads(raw: Any, default: Any) -> Any:
    try:
        got = json.loads(str(raw or ""))
    except ValueError:
        return default
    return got if isinstance(got, type(default)) else default


def run_version(conn, run_id: str, cache: dict) -> str:
    """``runs.app_version`` of *run_id* (cached per call chain); ``""`` when unknown."""
    if run_id not in cache:
        row = conn.execute(
            "SELECT app_version FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        cache[run_id] = str(row[0] or "") if row else ""
    return cache[run_id]


def is_new_version(conn, run_id: str, version: str) -> bool:
    """True when no EARLIER run (by insertion order) was on *version*."""
    row = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE app_version = ? AND run_id != ? AND rowid < "
        "COALESCE((SELECT rowid FROM runs WHERE run_id = ?), 0)",
        (version, run_id, run_id),
    ).fetchone()
    return int(row[0]) == 0


def belongs(conn, row: dict, scope: Scope) -> bool:
    version, cache = scope.ctx.version, scope.cache
    if row.get("app_version") == version:
        return True
    return (
        bool(row.get("source_run"))
        and run_version(conn, str(row["source_run"]), cache) == version
    )


def flag_event(conn, table: str, row_id: int) -> tuple:
    """``(detail, prior_status)`` of the latest flag event of a row, or ``("", "")``."""
    got = conn.execute(
        "SELECT detail, before_json FROM events WHERE table_name = ? AND row_id = ? AND "
        "(detail LIKE ? OR detail LIKE ? OR detail LIKE ?) ORDER BY id DESC LIMIT 1",
        (table, row_id) + _FLAG_LIKE,
    ).fetchone()
    if got is None:
        return "", ""
    before = loads(got[1], {})
    return str(got[0] or ""), str(before.get("status") or "active")


def where_for(table: str) -> str:
    return ACTIVE + (" AND COALESCE(screen_key, '') != ''" if table == "notes" else "")


def rid_of(row: dict) -> str:
    return str(loads(row.get("fp_json"), {}).get("rid") or "")


def ok_rids(steps: list) -> set:
    done = ("ok", "assert_pass", "visual_check", "done")
    return {
        (s.get("screen_key"), s.get("target_rid"))
        for s in steps
        if s.get("outcome") in done and s.get("target_rid")
    }


def num(item: Any, key: str) -> bool:
    try:
        return isinstance(item, dict) and int(cast("Any", item.get(key))) >= 0
    except (TypeError, ValueError, OverflowError):
        return False

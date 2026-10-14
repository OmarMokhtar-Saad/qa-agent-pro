"""Metadata queries for ``tools.suite_store`` (suite rows and checklists).

Every function takes an open connection from the facade's ``_run``.
"""

from __future__ import annotations

import json
import sqlite3
import time


def load_suite_meta(conn: sqlite3.Connection, suite_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, feature_text, source_url, created_at, created_by "
        "FROM suites WHERE id = ?",
        (suite_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "suite_id": row[0],
        "feature_text": row[1],
        "source_url": row[2],
        "created_at": row[3],
        "created_by": row[4],
    }


def list_recent(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT s.id, s.feature_text, s.source_url, s.created_at, "
        "(SELECT COUNT(*) FROM cases c WHERE c.suite_id = s.id) AS case_count "
        "FROM suites s ORDER BY s.created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "suite_id": r[0],
            "feature_text": r[1],
            "source_url": r[2],
            "created_at": r[3],
            "case_count": r[4],
        }
        for r in rows
    ]


def find_recent_by_source(
    conn: sqlite3.Connection, source_url: str, window_s: float | None
) -> dict | None:
    row = conn.execute(
        "SELECT id, created_at FROM suites WHERE source_url = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (source_url,),
    ).fetchone()
    if row is None:
        return None
    if window_s is not None and window_s > 0 and (time.time() - row[1]) > window_s:
        return None
    return {"suite_id": row[0], "created_at": row[1]}


def load_checklist(conn: sqlite3.Connection, suite_id: str) -> dict | None:
    row = conn.execute(
        "SELECT payload_json FROM checklists WHERE suite_id = ?", (suite_id,)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None

"""Stage C shared pieces: thresholds, the transition record, case objects, row helpers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits

#: table -> distinct runs a candidate needs (timings, edges, elements and screens are
#: gated by their own stages).
MIN_RUNS = {
    "notes": limits.GATE_MIN_RUNS,
    "lessons": limits.GATE_MIN_RUNS,
    "popups": limits.POPUP_MIN_RUNS,
    "mistakes": limits.MISTAKE_ACTIVE_RUNS,
    "shortcuts": limits.SHORTCUT_MIN_RUNS,
}
#: table -> the column a proposal replaces (tables without one go stale instead).
TEXT_COLUMN = {"notes": "text", "lessons": "text", "mistakes": "fix"}
KEEP_OUT = (
    "id",
    "created",
    "valid_from",
    "invalid_at",
    "status",
    "confirmed",
    "contradicted",
    "supersedes",
    "runs_json",
    "trust",
    "source_run",
    "proposal_json",
    "variant_of",
)
ERRORS = (sqlite3.Error, TypeError, ValueError, KeyError)


@dataclass
class Transition:
    table: str
    row_id: int
    old: str
    new: str
    why: str = ""


@dataclass
class Case:
    """One item a run's feedback or a rule touches."""

    table: str
    row: Any
    fb: dict
    run_id: str


@dataclass
class Variant:
    """A context-bound proposal: the new text and the env it applies to."""

    text: str
    env: dict


def runs_of(row: Any) -> int:
    """Distinct runs that upvoted *row*."""
    try:
        return len(
            {r for r in json.loads(str(row["runs_json"] or "[]")) if isinstance(r, str)}
        )
    except (ValueError, TypeError, KeyError, IndexError):
        return 0


def row_of(conn: Any, table: str, row_id: int):
    return conn.execute("SELECT * FROM %s WHERE id = ?" % table, (row_id,)).fetchone()


def is_open(row: Any) -> bool:
    return row is not None and row["invalid_at"] is None


def once(conn: Any, run_id: str, name: str) -> bool:
    """True the first time *name* runs for *run_id* (and records it)."""
    if kdb.has_marker(conn, run_id, name):
        return False
    kdb.put_marker(conn, run_id, name)
    return True

"""Write side of ``tools.suite_store``: suite rows, case rows, checklist rows.

Every function takes an open connection from the facade's ``_run`` and never
opens or closes one itself.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import NamedTuple

from tools.models import TestSuite

logger = logging.getLogger("tools.suite_store")


@dataclass(frozen=True)
class SuiteMeta:
    """Row metadata saved with a suite; ``provenance`` is stored as JSON."""

    feature_text: str = ""
    source_url: str | None = None
    created_by: str | None = None
    prep_id: str | None = None
    provenance: dict | None = None


class CaseStamp(NamedTuple):
    suite_id: str
    now: float


def provenance_json(provenance: dict | None) -> str | None:
    """JSON for the provenance column; a serialisation failure stores None."""
    try:
        return json.dumps(provenance) if provenance is not None else None
    except (TypeError, ValueError, RecursionError):
        logger.debug("save_suite: provenance serialization failed", exc_info=True)
        return None


def save(conn: sqlite3.Connection, suite: TestSuite, meta: SuiteMeta) -> str:
    now = time.time()
    with conn:  # transaction
        upsert_suite_row(
            conn,
            (
                suite.suite_id,
                meta.feature_text or "",
                meta.source_url,
                now,
                meta.created_by,
                meta.prep_id or None,
                provenance_json(meta.provenance),
            ),
        )
        target_id = surviving_suite_id(conn, suite.suite_id, meta.prep_id)
        insert_case_rows(conn, CaseStamp(target_id, now), suite)
    return target_id


# ONE atomic statement, deliberately NOT a SELECT-then-INSERT-or-
# UPDATE: each save_suite call runs on its OWN connection in its own
# thread, so a read-then-branch is a TOCTOU race -- two finalizes for
# the same prep (exactly what the retry-during-the-save-folder-dialog
# scenario produces) could both observe "no row yet" and both insert,
# which is the bug this is here to prevent. The partial UNIQUE index
# created in ensure_columns is what the conflict resolves against;
# SQLite serialises the two writers and the loser takes the DO UPDATE
# branch, keeping the ORIGINAL row's id (`excluded.id` is never
# assigned) and its prep_id.
#
# `INSERT OR REPLACE` is retained for the id-conflict case so a save
# that carries NO prep_id behaves byte-identically to before this fix.
def upsert_suite_row(conn: sqlite3.Connection, params: tuple) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO suites "
        "(id, feature_text, source_url, created_at, created_by, prep_id, "
        "provenance_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(prep_id) WHERE prep_id IS NOT NULL DO UPDATE SET "
        "feature_text = excluded.feature_text, "
        "source_url = excluded.source_url, "
        "created_at = excluded.created_at, "
        "created_by = excluded.created_by, "
        "provenance_json = excluded.provenance_json",
        params,
    )


def surviving_suite_id(
    conn: sqlite3.Connection, suite_id: str, prep_id: str | None
) -> str:
    if not prep_id:
        return suite_id
    # Which row survived. Safe to read: this connection already holds
    # the write lock taken by the upsert, so no other finalize can
    # interleave between the write and this lookup.
    row = conn.execute(
        "SELECT id FROM suites WHERE prep_id = ? LIMIT 1", (prep_id,)
    ).fetchone()
    return row[0] if row else suite_id


def insert_case_rows(
    conn: sqlite3.Connection, stamp: CaseStamp, suite: TestSuite
) -> None:
    # Replace the suite's cases wholesale so a re-save is idempotent.
    conn.execute("DELETE FROM cases WHERE suite_id = ?", (stamp.suite_id,))
    conn.executemany(
        "INSERT INTO cases (suite_id, stable_id, payload_json, version, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                stamp.suite_id,
                tc.stable_id,
                tc.model_dump_json(),
                1,
                stamp.now,
            )
            for tc in suite.test_cases
        ],
    )


# checklists.suite_id is an FK into suites and PRAGMA foreign_keys is
# ON, so a checklist for an in-session suite that was never persisted
# would raise IntegrityError and be silently dropped. Upsert a stub
# suites row first (INSERT OR IGNORE never touches an existing
# suite's feature_text) — the M1-web fix, first written for the
# web-run persistence block batch D3 deleted on 2026-08-15.
def save_checklist(conn: sqlite3.Connection, suite_id: str, payload: dict) -> str:
    now = time.time()
    with conn:  # transaction
        conn.execute(
            "INSERT OR IGNORE INTO suites (id, feature_text, created_at) "
            "VALUES (?, '', ?)",
            (suite_id, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO checklists "
            "(suite_id, payload_json, created_at) VALUES (?, ?, ?)",
            (suite_id, json.dumps(payload), now),
        )
    return suite_id

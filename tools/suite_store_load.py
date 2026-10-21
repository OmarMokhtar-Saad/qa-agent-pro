"""Read side of ``tools.suite_store``: rebuild a suite from its stored case rows.

Every function takes an open connection from the facade's ``_run``.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from config.settings_guard import never_raise
from tools.models import TestCase, TestSuite

logger = logging.getLogger("tools.suite_store")


def load_suite(conn: sqlite3.Connection, suite_id: str) -> TestSuite | None:
    row = conn.execute("SELECT id FROM suites WHERE id = ?", (suite_id,)).fetchone()
    if row is None:
        return None
    case_rows = conn.execute(
        "SELECT payload_json FROM cases WHERE suite_id = ? ORDER BY id ASC",
        (suite_id,),
    ).fetchall()
    return suite_from_rows(suite_id, case_rows)


# Forward compatibility: a row written by a NEWER build can carry fields
# this one does not know, and TestCase sets extra="forbid". Drop only the
# unknown keys and retry -- never guess at a value, never mutate a known
# one. Additive drift only; a narrowed constraint still fails.
def _lenient_retry(payload: str) -> tuple[TestCase, list[str]] | None:
    known = set(TestCase.model_fields)
    with never_raise() as g:
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError("case payload is not an object")
        dropped = sorted(k for k in raw if k not in known)
        if not dropped:
            raise ValueError("no unknown keys to drop")
        case = TestCase.model_validate({k: v for k, v in raw.items() if k in known})
    if g.failed:
        return None
    return case, dropped


# The error is returned even when the lenient retry recovers the case, so the
# caller keeps the first REAL reason.
def load_case_row(
    payload: str, suite_id: str
) -> tuple[TestCase | None, Exception | None]:
    """(case or None, the strict-parse error or None)."""
    with never_raise() as g:
        return TestCase.model_validate_json(payload), None
    first_err = g.exc if isinstance(g.exc, Exception) else None
    recovered = _lenient_retry(payload)
    if recovered is None:
        return None, first_err
    case, dropped = recovered
    logger.warning(
        "suite_store: case in suite %s carried unknown field(s) %s -- "
        "dropped them to load it (payload written by a newer build?)",
        suite_id,
        ", ".join(dropped),
    )
    return case, first_err


def _parse_cases(suite_id: str, case_rows: list) -> tuple[list[TestCase], int]:
    cases: list[TestCase] = []
    skipped = 0
    first_err = None
    for r in case_rows:
        case, err = load_case_row(r[0], suite_id)
        # Keep the REAL reason: the retry raises a synthetic error.
        first_err = first_err or err
        if case is not None:
            cases.append(case)
            continue
        skipped += 1
        _warn_case(suite_id, skipped, first_err)
    return cases, skipped


# At most a few lines: a wholly corrupt 64-case suite must not
# emit 64 tracebacks, and the SYNTHETIC retry error is not the
# interesting one -- first_err is.
def _warn_case(suite_id: str, skipped: int, first_err: Exception | None) -> None:
    if skipped <= 3:
        logger.warning(
            "suite_store: case in suite %s could not be loaded: %r",
            suite_id,
            first_err,
        )


def _warn_skipped(suite_id: str, skipped: int) -> None:
    if skipped:
        logger.warning(
            "suite_store: %d case(s) in suite %s could not be loaded and are "
            "NOT in the returned suite",
            skipped,
            suite_id,
        )


def suite_from_rows(suite_id: str, case_rows: list) -> TestSuite | None:
    if not case_rows:
        return None
    cases, skipped = _parse_cases(suite_id, case_rows)
    _warn_skipped(suite_id, skipped)
    if not cases:
        return None
    suite = TestSuite(suite_id=suite_id, test_cases=cases)
    # Never swallow a shortened suite: mirrors _dropped_note's rule that a
    # dropped count must reach the tester, not just the log. Private attr, the
    # same channel _checklist_artifacts already uses.
    if skipped:
        setattr(suite, "_load_skipped", skipped)
    return suite


# F21 (2026-09-02 audit): load_suite returns None both for an
# unknown id and for a suite whose every case row failed validation, so
# its callers could only ever report the first. This answers the question
# that separates them, and it is asked ONLY on the None path, so a normal
# load costs nothing.
def case_row_stats(conn: sqlite3.Connection, suite_id: str) -> tuple[bool, int]:
    """(does the suite ROW exist, how many case rows it has)."""
    row = conn.execute("SELECT id FROM suites WHERE id = ?", (suite_id,)).fetchone()
    if row is None:
        return False, 0
    counted = conn.execute(
        "SELECT COUNT(*) FROM cases WHERE suite_id = ?", (suite_id,)
    ).fetchone()
    return True, int((counted or (0,))[0] or 0)

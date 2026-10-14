"""Unfinished-prep disclosure listing (leaf of ``tools.prep_store``)."""

from __future__ import annotations

import asyncio
import json
import logging

from tools.prep_store_cfg import _expired, _expires_at
from tools.prep_store_conn import _connect, _has_touched_at, is_finalized_record

logger = logging.getLogger("tools.prep_store")


def _unfinished_rows(lim: int) -> list:
    """Candidate rows (4x ``lim``) with activity, newest first."""
    conn = _connect()
    try:
        touch_col = "p.touched_at" if _has_touched_at(conn) else "NULL"
        return conn.execute(
            "SELECT p.id, p.created_at, " + touch_col + ", p.payload_json, "
            "COUNT(s.id) AS staged FROM preps p "
            "LEFT JOIN prep_submissions s ON s.prep_id = p.id "
            "GROUP BY p.id "
            "HAVING staged > 0 OR " + touch_col + " IS NOT NULL "
            "ORDER BY p.created_at DESC LIMIT ?",
            (lim * 4,),
        ).fetchall()
    finally:
        conn.close()


def _expected_count(payload_json: str) -> int:
    """len(meta.expected_categories) of a stored payload; 0 when unreadable."""
    try:
        meta = (json.loads(payload_json) or {}).get("meta") or {}
        return len(meta.get("expected_categories") or [])
    except (ValueError, TypeError, AttributeError):
        return 0


def _is_finished(pid: str, payload_json: str) -> bool:
    # Fix 5b: a FINALIZED prep is finished work, never resumable. Dropping its
    # staged rows would NOT be enough -- the SQL activity filter also passes on
    # `touched_at IS NOT NULL`, and handle_get_category_job touches every prep
    # on the fan-out path, so a finished prep would still be offered here and
    # prep_unfinished_note would tell the tester to resume it with
    # qa_submit_category. Filtered on the stamp instead.
    try:
        return is_finalized_record(json.loads(payload_json) or {})
    except (TypeError, ValueError, RecursionError):
        logger.debug("could not read prep %s for a finalized check", pid)
        return False


def _skip_row(row: tuple) -> bool:
    pid, created, touched, payload_json, _staged = row
    if _expired(float(created), touched):
        return True
    return _is_finished(pid, payload_json)


def _entry(row: tuple) -> dict:
    pid, created, touched, payload_json, staged = row
    created_f = float(created)
    try:
        touched_f = float(touched or 0.0)
    except (TypeError, ValueError, OverflowError):
        touched_f = 0.0
    return {
        "prep_id": pid,
        "created_at": created_f,
        "touched_at": touched_f,
        "staged_count": int(staged or 0),
        "expected_count": _expected_count(payload_json),
        "expires_at": _expires_at(created_f, touched_f),
    }


# Newest first. The activity filter runs in SQL; only the TTL decision is
# left to Python (it depends on the sliding-TTL flag). ACCEPTED BOUND: the
# query takes 4x limit candidates, so with more than that many recent ACTIVE
# preps an older still-live one can fall outside the window -- acceptable for
# a newest-first disclosure that never blocks anything. payload_json is
# parsed ONLY for rows that survive the filters (bounded by limit) to read
# meta.expected_categories for the N/8 denominator. ACCEPTED SCOPE: the
# existing preps.created_by column is deliberately NOT filtered on -- this
# store is one SQLite file per install/user, so there is no cross-tenant
# separation to enforce, and the disclosure is flag-gated OFF by default.
def _list_unfinished_sync(limit: int) -> list[dict]:
    """Non-expired preps with real activity (touched or >=1 staged row)."""
    lim = max(1, int(limit))
    out: list[dict] = []
    for row in _unfinished_rows(lim):
        if _skip_row(row):
            continue
        out.append(_entry(row))
        if len(out) >= lim:
            break
    return out


async def list_unfinished_preps(limit: int = 3) -> dict:
    """Non-expired preps showing real activity (a fetched worker packet or
    >=1 staged category), newest first -- DISCLOSURE data for qa-doctor /
    qa_prepare_test_cases so an abandoned run stops evaporating silently
    (2026-07-31 incident). Read-only; never raises."""
    try:
        rows = await asyncio.to_thread(_list_unfinished_sync, int(limit))
        return {"error": None, "content": rows}
    except Exception as exc:
        logger.exception("prep_store.list_unfinished_preps failed")
        return {"error": str(exc), "content": None}

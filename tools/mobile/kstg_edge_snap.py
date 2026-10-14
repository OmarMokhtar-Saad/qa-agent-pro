"""Edge snapshot slice for the knowledge plugs (split out of knowledge_stage_edges)."""

from __future__ import annotations

from tools.mobile import knowledge_db, knowledge_limits

_COLS = ("id", "action", "target", "to_key", "count", "p50_ms", "p90_ms")


def _slim(row) -> dict:
    return {k: row[k] for k in _COLS}


def snapshot(conn, facts) -> dict:
    """Active edges by ``from_key`` (the plugs' read-only snapshot slice)."""
    out: dict = {}
    where = knowledge_db.Where(
        "status = 'active' AND invalid_at IS NULL",
        (),
        knowledge_limits.SNAPSHOT_MAX_ROWS,
    )
    for row in knowledge_db.rows(conn, "edges", where):
        out.setdefault(row["from_key"], []).append(_slim(row))
    return out

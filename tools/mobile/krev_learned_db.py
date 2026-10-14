"""The knowledge-db half of the "Learned this run" provider."""

from __future__ import annotations

from typing import Any

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits
from tools.mobile.krev_entry import Entry, learned_item

_TABLES = ("notes", "lessons", "mistakes", "popups", "shortcuts")


def _row_entry(table: str, row: dict) -> dict:
    text = str(row.get("text") or row.get("name") or row.get("title") or "")
    title = "%s #%s %s" % (table, row["id"], text)
    return learned_item(table, row["id"], Entry(title, str(row.get("status") or "")))


def _db_items(conn: Any, run_id: str, out: list) -> None:
    """Append the candidates this run wrote (``source_run == run_id``), every table."""
    for table in _TABLES:
        where = kdb.Where("source_run = ?", (run_id,), limits.LIST_PAGE_ROWS)
        for row in kdb.rows(conn, table, where):
            out.append(_row_entry(table, row))


def _recheck_item(conn: Any, out: list) -> None:
    n = conn.execute(
        "SELECT COUNT(*) FROM notes WHERE status = 'needs_recheck' AND invalid_at IS NULL"
    ).fetchone()[0]
    if n:
        entry = Entry("%d notes need re-check" % n, "needs_recheck")
        out.append(learned_item("notes", "", entry))

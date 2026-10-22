"""Suite families in ``suites``: the earlier suites of a source, and version stamps.

Every function takes an open connection from the facade's ``_run``.
"""

from __future__ import annotations

import logging
import sqlite3

from tools.suite_family import Earlier, source_key

logger = logging.getLogger("tools.suite_store")

_COLUMNS = (
    ("family_id", "TEXT"),
    ("version", "INTEGER"),
    ("prev_suite_id", "TEXT"),
    ("export_path", "TEXT"),
)
_LIST_SQL = (
    "SELECT s.id, s.version, s.created_at, s.source_url, s.feature_text, "
    "s.family_id, s.export_path, s.prep_id, "
    "(SELECT COUNT(*) FROM cases c WHERE c.suite_id = s.id) "
    "FROM suites s WHERE (s.source_url LIKE ? OR s.feature_text LIKE ? "
    "OR s.source_url = ? OR s.feature_text = ?) AND s.id != ? "
    "ORDER BY s.created_at ASC"
)


def ensure_family_columns(conn: sqlite3.Connection) -> None:
    """Add the family/version/export columns to a suites table that predates them.

    Each column is added on its own, so one failure never skips the others."""
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(suites)").fetchall()}
    except sqlite3.Error as exc:
        logger.warning("suites table_info failed: %s", exc)
        return
    for name, kind in _COLUMNS:
        if name not in have:
            _add_column(conn, name, kind)


def _add_column(conn: sqlite3.Connection, name: str, kind: str) -> None:
    try:
        conn.execute(f"ALTER TABLE suites ADD COLUMN {name} {kind}")
    except sqlite3.Error as exc:
        if "duplicate column" not in str(exc).lower():  # a concurrent add is fine
            logger.warning("suites.%s could not be added: %s", name, exc)


def _matches(key: str, source: str, row: tuple) -> bool:
    if key:
        return source_key(row[3]) == key or (not row[3] and source_key(row[4]) == key)
    return source in ((row[3] or "").strip(), (row[4] or "").strip())


def rows_for_source(conn: sqlite3.Connection, source: str, skip_id: str = "") -> list:
    """Raw rows of every suite saved for *source*, oldest first (ticket key, else exact)."""
    text = (source or "").strip()
    if not text:
        return []
    key = source_key(text)
    like = f"%{key}%" if key else text
    rows = conn.execute(_LIST_SQL, (like, like, text, text, skip_id)).fetchall()
    return [r for r in rows if _matches(key, text, r)]


def earlier_suites(conn: sqlite3.Connection, source: str, skip_id: str = "") -> list:
    """The :class:`Earlier` entries for *source*, oldest first; legacy rows rank by age."""
    rows = rows_for_source(conn, source, skip_id)
    return [
        Earlier(
            r[0],
            int(r[1] or i),
            float(r[2] or 0),
            int(r[8] or 0),
            r[6] or "",
            r[7] or "",
        )
        for i, r in enumerate(rows, start=1)
    ]


def stamp_family(conn: sqlite3.Connection, suite_id: str, source: str) -> None:
    """Give a freshly inserted suite its family id, version and previous-suite link.

    Never raises: a failed stamp is logged and the suite stays stored unversioned."""
    try:
        _stamp(conn, suite_id, source)
    except sqlite3.Error as exc:
        logger.warning("suite family stamp failed for %s: %s", suite_id, exc)


def _stamp(conn: sqlite3.Connection, suite_id: str, source: str) -> None:
    rows = rows_for_source(conn, source, suite_id)
    family = (rows[0][5] or rows[0][0]) if rows else suite_id
    prev = rows[-1][0] if rows else None
    version = max([int(r[1] or 0) for r in rows] + [len(rows)]) + 1
    conn.execute(
        "UPDATE suites SET family_id = ?, version = ?, prev_suite_id = ? "
        "WHERE id = ? AND family_id IS NULL",
        (family, version, prev, suite_id),
    )


def family_of(conn: sqlite3.Connection, suite_id: str) -> dict | None:
    """``{version, prev_suite_id, family_id}`` of a stored suite, else None."""
    row = conn.execute(
        "SELECT version, prev_suite_id, family_id FROM suites WHERE id = ?",
        (suite_id,),
    ).fetchone()
    if row is None:
        return None
    return {"version": row[0] or 1, "prev_suite_id": row[1], "family_id": row[2]}


def set_export_path(conn: sqlite3.Connection, suite_id: str, path: str) -> None:
    """Record the workbook path of a stored suite."""
    with conn:
        conn.execute("UPDATE suites SET export_path = ? WHERE id = ?", (path, suite_id))

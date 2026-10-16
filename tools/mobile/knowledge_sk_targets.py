"""Which ``sk1`` keys of one package map to which ``sk2`` screen keys."""

from __future__ import annotations

from tools.mobile import knowledge_db, screen_key
from tools.mobile.knowledge_versions_core import loads

_PAGE = 500
_LIVE = "invalid_at IS NULL AND status IN ('active', 'candidate', 'needs_recheck')"
COLS = (
    ("screens", "screen_key"),
    ("elements", "screen_key"),
    ("mistakes", "screen_key"),
    ("timings", "screen_key"),
    ("lessons", "screen_key"),
    ("notes", "scope_key"),
    ("edges", "from_key"),
    ("edges", "to_key"),
    ("shortcuts", "start_key"),
    ("shortcuts", "end_key"),
)


def live_sk1_keys(conn) -> set:
    """Every ``sk1:`` key a live row of any table still carries."""
    out: set = set()
    for table, col in COLS:
        sql = "SELECT DISTINCT %s FROM %s WHERE %s LIKE 'sk1:%%' AND %s"
        for row in conn.execute(sql % (col, table, col, _LIVE)).fetchall():
            out.add(row[0])
    return out


def _sk2_pages(conn):
    last = 1 << 62
    while True:
        where = knowledge_db.Where(
            "(screen_key LIKE 'sk2:%' OR screen_key LIKE 'sk3:%')"
            " AND invalid_at IS NULL AND id < ?",
            (last,),
            _PAGE,
        )
        page = knowledge_db.rows(conn, "screens", where)
        if not page:
            return
        yield from page
        last = page[-1]["id"]


def candidates(conn, package: str) -> dict:
    """``{sk1 key: {sk2 keys}}`` for the package's live ``sk1`` keys."""
    live = live_sk1_keys(conn)
    out: dict = {}
    for row in _sk2_pages(conn):
        anchors = loads(row.get("anchors_json"), [])
        old = screen_key.legacy_from(package, row.get("activity") or "", anchors)
        if old in live:
            out.setdefault(old, set()).add(row["screen_key"])
    return out

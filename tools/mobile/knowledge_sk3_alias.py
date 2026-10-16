"""Linking stored ``sk2`` screens to their ``sk3`` twin.

``sk2`` hashed the activity, so one screen could hold several keys. ``sk3`` does
not. Each learning run finds the live ``sk2`` screens whose anchors match exactly
one live ``sk3`` screen (``screen_match.SCREEN_MATCH_JACCARD``), records the old
key on the screen row (``remap_of``) and moves the rows stored under it with the
same re-key ``sk1`` used, so nothing is lost. Ambiguous or unmatched ``sk2``
keys are left alone and still resolve by their own key.
"""

from __future__ import annotations

from tools.mobile import knowledge_db, screen_match
from tools.mobile import knowledge_sk_legacy as sk_legacy
from tools.mobile.knowledge_versions_core import loads

_PAGE = 500


def _live(conn, prefix: str) -> dict:
    where = knowledge_db.Where(
        "screen_key LIKE ? AND invalid_at IS NULL", (prefix + ":%",), _PAGE
    )
    rows = knowledge_db.rows(conn, "screens", where)
    return {r["screen_key"]: r for r in rows}


def _anchors(row: dict) -> set:
    return set(loads(row.get("anchors_json"), []))


def aliases(conn) -> dict:
    """``{sk2 key: sk3 key}`` for every sk2 screen with one clear sk3 twin."""
    known = {k: _anchors(r) for k, r in _live(conn, "sk3").items()}
    out: dict = {}
    for key, row in _live(conn, "sk2").items():
        twin = screen_match.nearest(_anchors(row), known)
        if twin:
            out[key] = twin
    return out


def _mark(conn, row: dict, link: tuple) -> None:
    twin, run_id = link
    status = knowledge_db.Status(
        row["id"], row["status"], {"remap_of": twin}, run_id, "sk2->sk3"
    )
    knowledge_db.set_status(conn, "screens", status)


def migrate(conn, run_id: str) -> int:
    """Re-key rows stored under an sk2 key to its sk3 twin; returns rows moved."""
    old, done = _live(conn, "sk2"), 0
    for key, twin in aliases(conn).items():
        _mark(conn, old[key], (twin, run_id))
        for table in sk_legacy.KEY_COLS:
            done += sk_legacy.move_table(conn, (table, key, twin, run_id))
    return done

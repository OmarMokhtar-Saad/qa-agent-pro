"""Report provider: items that need the tester's review (contract W2d).

Disputed notes (with keep/replace/narrow/delete hints), similar-note update
proposals and pending reflections. Read-only over the knowledge db; never raises.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from tools.mobile import app_knowledge, knowledge_feedback
from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits

logger = logging.getLogger(__name__)

_RESOLVE = "qa_mobile_knowledge action=resolve item_id=notes:%s verdict=<keep|replace|narrow|delete>"


def _item(table: str, ident: object, title: str, detail: str, hint: str = "") -> dict:
    return {
        "table": table,
        "id": str(ident),
        "title": title,
        "detail": detail,
        "action_hint": hint,
    }


def _disputed(conn: object) -> list:
    out: list = []
    where = "(status = 'disputed' OR proposal_json != '') AND invalid_at IS NULL"
    for row in kdb.rows(conn, "notes", where, (), limit=limits.LIST_PAGE_ROWS):
        try:
            prop = json.loads(row.get("proposal_json") or "{}")
        except ValueError:
            prop = {}
        detail = "disputed: %s" % str(prop.get("observation") or "")[:120]
        if prop.get("proposed"):
            detail += " -> proposed: %s" % str(prop["proposed"])[:120]
        out.append(
            _item(
                "notes",
                row["id"],
                "note #%s %s" % (row["id"], row.get("text") or ""),
                detail,
                _RESOLVE % row["id"],
            )
        )
    return out


def _similar(conn: object) -> list:
    out: list = []
    where = "status = 'candidate' AND invalid_at IS NULL"
    for row in kdb.rows(conn, "notes", where, (), limit=limits.LIST_PAGE_ROWS):
        other = knowledge_feedback.match_existing(conn, row)
        if other is not None and other != row["id"]:
            hint = (
                "qa_mobile_knowledge action=edit item_id=notes:%s text=<new text>"
                % other
            )
            out.append(
                _item(
                    "notes",
                    row["id"],
                    "note #%s is similar to #%s" % (row["id"], other),
                    "update #%s?" % other,
                    hint,
                )
            )
    return out


def _reflections(conn: object) -> list:
    pending = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE reflect_state = 'pending'"
    ).fetchone()[0]
    if not pending:
        return []
    hint = "qa_mobile_knowledge action=reflect"
    return [
        _item(
            "runs",
            "",
            "%d reflection(s) pending" % pending,
            "answer them to teach the app notes",
            hint,
        )
    ]


def build(package: str, run_id: str, summary: object) -> list:
    out: list = []
    try:
        path = app_knowledge.db_path(package)
        if path is None or not path.exists():
            return out
        conn = app_knowledge._connect(path, write=False)
        try:
            out = _disputed(conn) + _similar(conn) + _reflections(conn)
        finally:
            conn.close()
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError):
        logger.exception("review provider failed")
    return out


def review_lines(package: str) -> list:
    """The review items as plain lines for ``qa_mobile_knowledge action=review``."""
    return [
        "%s | %s | %s" % (i["title"], i["detail"], i["action_hint"])
        for i in build(package, "", {})
    ]

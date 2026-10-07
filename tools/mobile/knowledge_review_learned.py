"""Report provider: "Learned this run" (contract 1.8).

``build`` reads ``summary["learned"]`` (a list of dicts with table, id, title or
text, detail, status) from ``runs.summary_json``; ``guards`` lists the notes that
fired in the run, read from the checkpointed cases' step entries
(``entry["knowledge"]``). Neither raises.
"""

from __future__ import annotations

import logging
import sqlite3

from tools.mobile import app_knowledge
from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits

logger = logging.getLogger(__name__)


_TABLES = ("notes", "lessons", "mistakes", "popups", "shortcuts")


def _item(table: str, ident: object, title: str, detail: str) -> dict:
    hint = (
        "qa_mobile_knowledge action=confirm table=%s item_id=%s" % (table, ident)
        if ident != ""
        else ""
    )
    return {
        "table": table,
        "id": str(ident),
        "title": title,
        "detail": detail,
        "action_hint": hint,
    }


def _from_db(package: str, run_id: str) -> list:
    """Candidates this run wrote (``source_run == run_id``) plus a re-check count."""
    out: list = []
    try:
        path = app_knowledge.db_path(package)
        if path is None or not path.exists() or not run_id:
            return out
        conn = app_knowledge._connect(path, write=False)
        try:
            for table in _TABLES:
                for row in kdb.rows(
                    conn,
                    table,
                    "source_run = ?",
                    (run_id,),
                    limit=limits.LIST_PAGE_ROWS,
                ):
                    text = str(
                        row.get("text") or row.get("name") or row.get("title") or ""
                    )
                    out.append(
                        _item(
                            table,
                            row["id"],
                            "%s #%s %s" % (table, row["id"], text),
                            str(row.get("status") or ""),
                        )
                    )
            n = conn.execute(
                "SELECT COUNT(*) FROM notes WHERE status = 'needs_recheck' AND invalid_at IS NULL"
            ).fetchone()[0]
            if n:
                out.append(
                    _item("notes", "", "%d notes need re-check" % n, "needs_recheck")
                )
        finally:
            conn.close()
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError):
        logger.exception("learned db provider failed")
    return out


def _from_summary(summary: object) -> list:
    out: list = []
    if not isinstance(summary, dict):
        return out
    for key in ("heals", "skipped_env"):
        value = summary.get(key)
        for raw in (value if isinstance(value, list) else [])[: limits.LIST_PAGE_ROWS]:
            out.append(_item("runs", "", "%s: %s" % (key, str(raw)[:160]), key))
    return out


def build(package: str, run_id: str, summary: object) -> list:
    out: list = []
    try:
        learned = summary.get("learned") if isinstance(summary, dict) else None
        for raw in (learned if isinstance(learned, list) else [])[
            : limits.LIST_PAGE_ROWS
        ]:
            if not isinstance(raw, dict):
                continue
            table, ident = str(raw.get("table") or ""), str(raw.get("id") or "")
            out.append(
                {
                    "table": table,
                    "id": ident,
                    "title": str(raw.get("title") or raw.get("text") or ""),
                    "detail": str(raw.get("detail") or raw.get("status") or ""),
                    "action_hint": (
                        "qa_mobile_knowledge action=confirm table=%s item_id=%s"
                        % (table, ident)
                        if table and ident
                        else ""
                    ),
                }
            )
        out += _from_db(package, run_id)
        out += _from_summary(summary)
    except Exception:
        logger.exception("learned provider failed")
    return out


def _fired(node: object, depth: int = 0) -> list:
    found: list = []
    if depth > 6:
        return found
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "knowledge" and isinstance(value, list):
                found += [v for v in value if isinstance(v, dict) and "note_id" in v]
            else:
                found += _fired(value, depth + 1)
    elif isinstance(node, list):
        for value in node[:500]:
            found += _fired(value, depth + 1)
    return found


def guards(run_id: str) -> list:
    out: list = []
    try:
        from tools.mobile import run_store

        cases = (run_store.list_cases(run_id) or {}).get("content") or []
        for fired in _fired(cases)[: limits.LIST_PAGE_ROWS]:
            out.append(
                {
                    "table": "notes",
                    "id": str(fired.get("note_id") or ""),
                    "title": "note #%s %s"
                    % (fired.get("note_id"), fired.get("kind") or ""),
                    "detail": "%s, %s ms"
                    % (fired.get("outcome") or "", int(fired.get("ms") or 0)),
                    "action_hint": "",
                }
            )
    except Exception:
        logger.exception("guards provider failed")
    return out

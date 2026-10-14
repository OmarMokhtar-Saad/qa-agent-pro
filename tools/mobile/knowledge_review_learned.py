"""Report provider: "Learned this run" (contract 1.8).

``build`` reads ``summary["learned"]`` (a list of dicts with table, id, title or
text, detail, status) from ``runs.summary_json``; ``guards`` lists the notes that
fired in the run, read from the checkpointed cases' step entries
(``entry["knowledge"]``). Neither raises.
"""

from __future__ import annotations

import logging
import sqlite3

from tools.mobile import app_knowledge, run_store
from tools.mobile import knowledge_limits as limits
from tools.mobile.krev_entry import Entry, confirm_hint
from tools.mobile.krev_entry import learned_item as _item
from tools.mobile.krev_guards import _fired, _guard_item
from tools.mobile.krev_learned_db import _db_items, _recheck_item

logger = logging.getLogger(__name__)


def _from_db(package: str, run_id: str) -> list:
    """Candidates this run wrote (``source_run == run_id``) plus a re-check count."""
    out: list = []
    try:
        path = app_knowledge.db_path(package)
        if path is None or not path.exists() or not run_id:
            return out
        conn = app_knowledge._connect(path, write=False)
        try:
            _db_items(conn, run_id, out)
            _recheck_item(conn, out)
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
            title = "%s: %s" % (key, str(raw)[:160])
            out.append(_item("runs", "", Entry(title, key)))
    return out


def _learned_row(raw: dict) -> dict:
    table, ident = str(raw.get("table") or ""), str(raw.get("id") or "")
    return {
        "table": table,
        "id": ident,
        "title": str(raw.get("title") or raw.get("text") or ""),
        "detail": str(raw.get("detail") or raw.get("status") or ""),
        "action_hint": confirm_hint(table, ident) if table and ident else "",
    }


def build(package: str, run_id: str, summary: object) -> list:
    out: list = []
    try:
        learned = summary.get("learned") if isinstance(summary, dict) else None
        rows = (learned if isinstance(learned, list) else [])[: limits.LIST_PAGE_ROWS]
        for raw in rows:
            if isinstance(raw, dict):
                out.append(_learned_row(raw))
        out += _from_db(package, run_id)
        out += _from_summary(summary)
    except Exception:
        logger.exception("learned provider failed")
    return out


def guards(run_id: str) -> list:
    out: list = []
    try:
        cases = (run_store.list_cases(run_id) or {}).get("content") or []
        for fired in _fired(cases)[: limits.LIST_PAGE_ROWS]:
            out.append(_guard_item(fired))
    except Exception:
        logger.exception("guards provider failed")
    return out

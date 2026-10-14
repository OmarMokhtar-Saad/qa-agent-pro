"""``qa_mobile_knowledge`` export action."""

from __future__ import annotations

import json
from contextlib import closing

from tools.mobile import app_knowledge, app_store, knowledge_limits
from tools.mobile import knowledge_db as kdb
from tools.mobile.ktool_core import TABLES, _refuse
from tools.untrusted import single_line, wrap_untrusted

_EXPORT_STATUS = "status IN ('active', 'candidate') AND invalid_at IS NULL"


def _export_tables(conn) -> dict:
    tables: dict = {}
    left = knowledge_limits.EXPORT_MAX_ROWS
    for name in TABLES:
        cap = min(left, knowledge_limits.SNAPSHOT_MAX_ROWS)
        found = (
            kdb.rows(conn, name, kdb.Where(_EXPORT_STATUS, (), cap)) if left > 0 else []
        )
        tables[name] = found
        left -= len(found)
    return tables


def _export_note(saved: dict) -> str:
    if not saved.get("error"):
        return ""
    return "\n(not written to export.json: %s)" % single_line(saved["error"], 100)


def _export(package: str) -> str:
    path = app_knowledge.db_path(package)
    if path is None or not path.exists():
        return _refuse("no knowledge saved for `%s` yet" % package)
    with closing(app_knowledge._connect(path, write=False)) as conn:
        tables = _export_tables(conn)
    body = {"package": package, "tables": tables}
    saved = app_store.write_json(package, "export.json", body)
    note = _export_note(saved)
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return (
        wrap_untrusted(
            "app_knowledge_export", text, limit=knowledge_limits.PAYLOAD_CHARS
        )
        + note
    )

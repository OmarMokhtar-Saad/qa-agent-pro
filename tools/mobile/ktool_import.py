"""``qa_mobile_knowledge`` import action: rows arrive as candidates only."""

from __future__ import annotations

import json
from functools import partial

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits
from tools.mobile.ktool_args import Raw
from tools.mobile.ktool_core import TABLES, _refuse, _said, _write
from tools.mobile.ktool_imp_rules import (
    _IMPORT_SHARE,
    _IMPORTABLE,
    _SKIP_IMPORT,
    _import_note,
)
from tools.mobile.ktool_import_learned import _import_learned


def _import_room(conn, table: str) -> bool:
    cap = kdb._cap(table)
    if not cap:
        return True
    (held,) = conn.execute(
        "SELECT COUNT(*) FROM %s WHERE trust = 'imported' AND invalid_at IS NULL"
        % table
    ).fetchone()
    return held < int(cap * _IMPORT_SHARE)


def _import_row(conn, raw: Raw) -> bool:
    table = raw.table
    if table not in _IMPORTABLE or not _import_room(conn, table):
        return False
    values = {
        k: v
        for k, v in raw.values.items()
        if k not in _SKIP_IMPORT and isinstance(k, str)
    }
    values.update(
        trust="imported", status="candidate", source_run="import", runs_json="[]"
    )
    ok = _import_note(values) if table == "notes" else _import_learned(table, values)
    if not ok:
        return False
    write = kdb.Write(values, run_id=raw.run_id, why="import")
    return kdb.insert(conn, table, write) is not None


def _import_rows(tables: dict):
    for name, listing in tables.items():
        if name not in TABLES or not isinstance(listing, list):
            continue
        for raw in listing:
            yield name, raw


def _import_work(conn, tables: dict, run_id: str) -> str:
    seen = kept = 0
    for name, raw in _import_rows(tables):
        seen += 1
        if seen > knowledge_limits.IMPORT_MAX_ROWS:
            return _said(
                "stopped at %d rows; imported %d as candidates"
                % (knowledge_limits.IMPORT_MAX_ROWS, kept)
            )
        if isinstance(raw, dict) and _import_row(conn, Raw(name, raw, run_id)):
            kept += 1
    return _said("imported %d of %d rows as candidates (never active)" % (kept, seen))


def _parse_tables(payload: str):
    """The export's ``tables`` dict, or ``None`` when the payload is no export."""
    try:
        body = json.loads(payload or "{}")
        tables = body.get("tables") if isinstance(body, dict) else None
        if not isinstance(tables, dict):
            raise ValueError
    except (ValueError, AttributeError):
        return None
    return tables


def _import(package: str, payload: str, run_id: str) -> str:
    tables = _parse_tables(payload)
    if tables is None:
        return _refuse('`payload` must be an export: {"tables": {<table>: [rows]}}')
    work = partial(_import_work, tables=tables, run_id=run_id or "import")
    return _write(package, work)

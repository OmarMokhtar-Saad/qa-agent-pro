"""The core inject source: notes, lessons and mistakes from the snapshot."""

from __future__ import annotations

from typing import Any

from tools.mobile import knowledge_limits as limits
from tools.mobile.knowledge_inject_block import _line

_TRUST_PRIO = {"tester": 0, "host": 1}
_SCREEN_NOTE_KINDS = ("fact", "precondition")


def _usable(row: dict) -> bool:
    """Only active rows (a note may be needs_recheck); a disputed note is never here."""
    if row.get("proposal_json") or row.get("invalid_at") is not None:
        return False
    table = row.get("_table")
    if table == "notes":
        return row.get("status") in ("active", "needs_recheck")
    return row.get("status") == "active"


def _row_item(row: dict) -> dict:
    table = str(row.get("_table") or "lessons")
    rid = row.get("id")
    net = int(row.get("confirmed") or 0) - int(row.get("contradicted") or 0)
    return {
        "id": "%s:%s" % (table, rid),
        "table": table,
        "num": rid,
        "net": net,
        "prio": _TRUST_PRIO.get(str(row.get("trust") or ""), 2),
        "text": row.get("text"),
    }


def _mistake_item(row: dict) -> dict | None:
    if row.get("status") not in (None, "active") or row.get("invalid_at") is not None:
        return None
    bad = _line(row.get("bad_pattern"), limits.RULE_CHARS)
    if not bad:
        return None
    fix = _line(row.get("fix"), limits.RULE_CHARS)
    text = "Don't %s; %s" % (bad, fix) if fix else "Don't %s" % bad
    return {
        "id": "mistakes:%s" % row.get("id"),
        "table": "mistakes",
        "num": row.get("id"),
        "net": 0,
        "prio": 2,
        "text": text,
    }


def core(snap: dict, key: Any) -> list:
    out: list = []
    if key is None:
        rows = [
            r for r in snap.get("global") or [] if isinstance(r, dict) and _usable(r)
        ]
        return [_row_item(r) for r in rows]
    for row in (snap.get("lessons") or {}).get(key) or []:
        if not isinstance(row, dict) or not _usable(row):
            continue
        if row.get("_table") == "notes" and row.get("kind") not in _SCREEN_NOTE_KINDS:
            continue
        out.append(_row_item(row))
    for row in (snap.get("mistakes") or {}).get(key) or []:
        item = _mistake_item(row) if isinstance(row, dict) else None
        if item is not None:
            out.append(item)
    return out

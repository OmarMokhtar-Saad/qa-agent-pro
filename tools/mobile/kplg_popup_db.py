"""Popup plug persistence: the in-memory snapshot rows and the fired-counter flush."""

from __future__ import annotations

import json

from tools.mobile import knowledge_db, knowledge_limits


def snapshot(conn, facts) -> dict:
    """Active popups only: ``[{id, signature, dismiss}]``."""
    out = []
    for row in knowledge_db.rows(
        conn,
        "popups",
        knowledge_db.Where(
            "status = 'active' AND invalid_at IS NULL",
            (),
            knowledge_limits.SNAPSHOT_MAX_ROWS,
        ),
    ):
        try:
            dismiss = json.loads(row.get("dismiss_json") or "{}")
        except ValueError:
            dismiss = {}
        out.append({"id": row["id"], "signature": row["signature"], "dismiss": dismiss})
    return {"popups": out}


def _fired_items(events: list) -> list:
    fired = [
        e
        for e in events or []
        if isinstance(e, dict) and e.get("kind") == "popup_fired"
    ]
    return sorted({str(e.get("item")) for e in fired})


def _count_fired(conn, run_id: str, item: str) -> None:
    parsed = knowledge_db.parse_item_id(item)
    found = []
    if parsed and parsed[0] == "popups":
        found = knowledge_db.rows(
            conn,
            "popups",
            knowledge_db.Where("id = ? AND invalid_at IS NULL", (parsed[1],), 1),
        )
    if found:
        key = {"signature": found[0]["signature"]}
        knowledge_db.upsert_counter(
            conn,
            "popups",
            knowledge_db.Counter(key, {}, run_id=run_id, why="fired"),
        )


def flush(package: str, run_id: str, events: list) -> None:
    """Record each fired popup once as a counter event (no counter changes)."""
    items = _fired_items(events)
    if not items:
        return
    conn = knowledge_db.open_rw(package)
    if conn is None:
        return
    try:
        for item in items:
            _count_fired(conn, run_id, item)
    finally:
        conn.close()

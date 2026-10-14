"""Status changes of the knowledge db."""

from __future__ import annotations

import logging
import time

from tools.mobile import knowledge_schema
from tools.mobile.knowledge_db_args import Ev, Hit, Refusal, Status
from tools.mobile.knowledge_db_core import (
    CLOSED,
    ERRORS,
    NO_WRITE,
    STATUSES,
    dump,
    event,
    finish,
    owns,
    refuse,
    safe_rollback,
    secret,
)

logger = logging.getLogger("tools.mobile.knowledge_db")


def _extra(conn, table: str, st: Status) -> dict:
    have = set(knowledge_schema.table_columns(conn, table))
    return {
        k: dump(v)
        for k, v in (st.cols or {}).items()
        if k in have and k not in NO_WRITE | {"status"}
    }


def _vet(conn, table: str, st: Status) -> dict | None:
    """The extra columns, or None after logging a secret refusal (no finish)."""
    extra = _extra(conn, table, st)
    reason = secret(extra, st.run_id)
    if reason:
        refuse(conn, table, Refusal(st.run_id, st.why, "secret " + reason))
        return None
    return extra


def _load(conn, table: str, row_id: int):
    return conn.execute("SELECT * FROM %s WHERE id = ?" % table, (row_id,)).fetchone()


def _apply(conn, hit: Hit, st: Status) -> None:
    old = hit.row
    new = {"status": st.status, **st.cols}
    if st.status in CLOSED and old["invalid_at"] is None:
        new["invalid_at"] = time.time()
    before = {c: old[c] for c in new}
    sets = ", ".join("%s = ?" % c for c in new)
    conn.execute(
        "UPDATE %s SET %s WHERE id = ?" % (hit.table, sets),
        list(new.values()) + [st.row_id],
    )
    event(conn, "status", Ev(hit.table, st.row_id, st.run_id, st.why, before))


def _update(conn, table: str, st: Status) -> bool:
    """False (no write, no finish) when the row is missing."""
    old = _load(conn, table, st.row_id)
    if old is None:
        return False
    _apply(conn, Hit(table, old), st)
    return True


def set_status(conn, table: str, st: Status) -> bool:
    """Set a row's status (and any extra columns). True when a row changed."""
    own = owns(conn)
    try:
        st = st._replace(run_id=str(st.run_id or ""))
        if st.status not in STATUSES or table not in knowledge_schema.LEARNED_TABLES:
            return False
        extra = _vet(conn, table, st)
        if extra is None:
            finish(conn, own, True)
            return False
        if not _update(conn, table, st._replace(cols=extra)):
            return False
        finish(conn, own, True)
        return True
    except ERRORS:
        logger.info("knowledge db: set_status failed", exc_info=True)
        safe_rollback(conn, own)
        return False

from __future__ import annotations

from tools.mobile import knowledge_db, knowledge_limits
from tools.mobile.knowledge_versions_core import (
    FLAG_TABLES,
    Flagged,
    Scope,
    VerCtx,
    belongs,
    flag_event,
    loads,
    num,
    ok_rids,
    rid_of,
    where_for,
)

_FLAGGED = "status = 'needs_recheck' AND invalid_at IS NULL"


def _flag_row(conn, fl: Flagged, ctx: VerCtx) -> int:
    status = knowledge_db.Status(
        fl.row["id"],
        "needs_recheck",
        {"recheck_at": ctx.version},
        ctx.run_id,
        "recheck@" + ctx.version,
    )
    return int(knowledge_db.set_status(conn, fl.table, status))


def _flag_table(conn, table: str, scope: Scope) -> int:
    done = 0
    where = knowledge_db.Where(where_for(table), (), knowledge_limits.SNAPSHOT_MAX_ROWS)
    for row in knowledge_db.rows(conn, table, where):
        if not belongs(conn, row, scope):
            done += _flag_row(conn, Flagged(table, row), scope.ctx)
    return done


def flag_version(conn, version: str, run_id: str) -> int:
    """Mark every active item not learned on *version* ``needs_recheck``."""
    scope = Scope(VerCtx(version, run_id), {})
    return sum(_flag_table(conn, table, scope) for table in FLAG_TABLES)


def _restore(conn, fl: Flagged, ctx: VerCtx) -> bool:
    detail, prior = flag_event(conn, fl.table, fl.row["id"])
    if not detail.endswith(ctx.version) and not detail.startswith(
        "missing@" + ctx.version
    ):
        return False
    if fl.row.get("status") != "needs_recheck":
        return False
    cols = {"recheck_at": "", "app_version": ctx.version}
    status = knowledge_db.Status(
        fl.row["id"], prior or "active", cols, ctx.run_id, "verified@" + ctx.version
    )
    return knowledge_db.set_status(conn, fl.table, status)


def _flagged(conn, table: str, limit: int = 500) -> list:
    return knowledge_db.rows(conn, table, knowledge_db.Where(_FLAGGED, (), limit))


def _touched(conn, table: str, run_id: str) -> list:
    """Flagged rows whose upvote list names this run (counters go through knowledge_db)."""
    return [r for r in _flagged(conn, table) if run_id in loads(r.get("runs_json"), [])]


def _restore_elements(conn, seen: set, ctx: VerCtx) -> int:
    rows = _flagged(conn, "elements")
    hits = [r for r in rows if (r.get("screen_key"), rid_of(r)) in seen]
    return sum(int(_restore(conn, Flagged("elements", r), ctx)) for r in hits)


def _restore_notes(conn, fired: set, ctx: VerCtx) -> int:
    hits = [r for r in _flagged(conn, "notes") if r["id"] in fired]
    return sum(int(_restore(conn, Flagged("notes", r), ctx)) for r in hits)


def _restore_touched(conn, ctx: VerCtx) -> int:
    done = 0
    for table in ("mistakes", "popups", "shortcuts"):
        for row in _touched(conn, table, ctx.run_id):
            done += int(_restore(conn, Flagged(table, row), ctx))
    return done


def _fired_ids(steps: list) -> set:
    return {
        int(f["note_id"])
        for s in steps
        for f in s.get("fired") or []
        if num(f, "note_id")
    }


def restore_confirmed(conn, steps: list, ctx: VerCtx) -> int:
    """Items confirmed in this run on *version* return to their prior status."""
    seen, fired = ok_rids(steps), _fired_ids(steps)
    done = _restore_elements(conn, seen, ctx)
    done += _restore_notes(conn, fired, ctx)
    return done + _restore_touched(conn, ctx)


def _reactivate_row(conn, fl: Flagged, scope: Scope) -> int:
    detail, prior = flag_event(conn, fl.table, fl.row["id"])
    ctx = scope.ctx
    if not detail.startswith("recheck@") or detail.endswith(ctx.version):
        return 0
    if not belongs(conn, fl.row, scope):
        return 0
    status = knowledge_db.Status(
        fl.row["id"],
        prior or "active",
        {"recheck_at": ""},
        ctx.run_id,
        "downgrade@" + ctx.version,
    )
    return int(knowledge_db.set_status(conn, fl.table, status))


def reactivate_downgrade(conn, version: str, run_id: str) -> int:
    """A run on a version seen before: rows that belong to *version* and were
    flagged for another version go back to their prior status."""
    scope = Scope(VerCtx(version, run_id), {})
    done = 0
    for table in FLAG_TABLES:
        for row in _flagged(conn, table):
            done += _reactivate_row(conn, Flagged(table, row), scope)
    return done

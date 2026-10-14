from __future__ import annotations

from tools.mobile import knowledge_db
from tools.mobile.knowledge_versions_core import (
    ACTIVE,
    Flagged,
    Gone,
    VerCtx,
    loads,
    rid_of,
)


def _why(gone: Gone) -> str:
    return "missing@%s:%s" % (gone.version, gone.rid)


def _disable(conn, fl: Flagged, gone: Gone) -> int:
    why = _why(gone)
    cols = {"recheck_at": why.split("@")[1].split(":")[0]}
    status = knowledge_db.Status(fl.row["id"], "needs_recheck", cols, gone.run_id, why)
    return int(knowledge_db.set_status(conn, fl.table, status))


def _shortcut_uses(row: dict, rid: str) -> bool:
    for step in loads(row.get("steps_json"), []):
        target = step.get("target") if isinstance(step, dict) else None
        if isinstance(step, dict) and rid in (
            step.get("rid"),
            (target or {}).get("rid") if isinstance(target, dict) else None,
        ):
            return True
    return False


def disable_dependents(conn, gone: Gone) -> int:
    """An element missing on *key*: disable its notes and shortcuts for *version*."""
    done = 0
    where = knowledge_db.Where(ACTIVE + " AND screen_key = ?", (gone.key,), 500)
    for row in knowledge_db.rows(conn, "notes", where):
        if str(loads(row.get("when_json"), {}).get("rid") or "") == gone.rid:
            done += _disable(conn, Flagged("notes", row), gone)
    for row in knowledge_db.rows(
        conn, "shortcuts", knowledge_db.Where(ACTIVE, (), 500)
    ):
        if _shortcut_uses(row, gone.rid):
            done += _disable(conn, Flagged("shortcuts", row), gone)
    return done


def _mark_missing(conn, gone: Gone) -> None:
    where = knowledge_db.Where(
        "screen_key = ? AND invalid_at IS NULL", (gone.key,), 500
    )
    for row in knowledge_db.rows(conn, "elements", where):
        if rid_of(row) == gone.rid and row.get("missing_at") != gone.version:
            status = knowledge_db.Status(
                row["id"],
                row["status"],
                {"missing_at": gone.version},
                gone.run_id,
                _why(gone),
            )
            knowledge_db.set_status(conn, "elements", status)


def apply_missing(conn, steps: list, ctx: VerCtx) -> int:
    """Elements the queue and the heal could not find (outcome ``missing_element``)."""
    done, seen = 0, set()
    for rec in steps:
        slot = (rec.get("screen_key"), rec.get("target_rid"))
        if rec.get("outcome") != "missing_element" or not slot[1] or slot in seen:
            continue
        if str(slot[0]).startswith("sk1d:"):
            continue
        seen.add(slot)
        gone = Gone(slot[0], slot[1], ctx.version, ctx.run_id)
        _mark_missing(conn, gone)
        done += disable_dependents(conn, gone)
    return done

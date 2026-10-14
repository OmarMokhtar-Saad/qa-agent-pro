from __future__ import annotations

from tools.mobile import knowledge_db, knowledge_limits, screen_key
from tools.mobile.knowledge_versions_core import ACTIVE, Remap, VerCtx, loads

_NOTE_COLS = (
    "kind",
    "text",
    "when_json",
    "then_json",
    "scope_key",
    "trust",
    "element_fp",
)


def _best_old(conn, new: dict, ctx: VerCtx) -> tuple:
    anchors = loads(new.get("anchors_json"), [])
    best = ("", 0.0)
    where = knowledge_db.Where(
        "invalid_at IS NULL AND app_version != ?", (ctx.version,), 500
    )
    for old in knowledge_db.rows(conn, "screens", where):
        if old["screen_key"] == new["screen_key"]:
            continue
        score = screen_key.similarity(anchors, loads(old.get("anchors_json"), []))
        if score > best[1]:
            best = (old["screen_key"], score)
    return best


def _element_values(row: dict, link: Remap, ctx: VerCtx) -> dict:
    fp = loads(row.get("fp_json"), {})
    return {
        "screen_key": link.new_key,
        "fp_json": row["fp_json"],
        "locators_json": row["locators_json"],
        "fp_hash": screen_key.fp_hash(dict(fp, remap=link.new_key)),
        "bound_field": row["bound_field"],
        "status": "needs_recheck",
        "recheck_at": ctx.version,
        "supersedes": row["id"],
        "trust": row["trust"],
        "app_version": ctx.version,
    }


def _note_values(row: dict, link: Remap, ctx: VerCtx) -> dict:
    values = {k: row[k] for k in _NOTE_COLS}
    values.update(
        screen_key=link.new_key,
        status="needs_recheck",
        recheck_at=ctx.version,
        supersedes=row["id"],
        app_version=ctx.version,
    )
    return values


def _copy_row(conn, table: str, w: knowledge_db.Write) -> int:
    return int(knowledge_db.insert(conn, table, w) is not None)


def _write(values: dict, ctx: VerCtx) -> knowledge_db.Write:
    return knowledge_db.Write(values, ctx.run_id, "remap@" + ctx.version)


def _old_rows(conn, table: str, link: Remap) -> list:
    where = knowledge_db.Where("screen_key = ? AND " + ACTIVE, (link.old_key,), 500)
    return knowledge_db.rows(conn, table, where)


def _copy_elements(conn, link: Remap, ctx: VerCtx) -> int:
    rows = _old_rows(conn, "elements", link)
    return sum(
        _copy_row(conn, "elements", _write(_element_values(r, link, ctx), ctx))
        for r in rows
    )


def _copy_notes(conn, link: Remap, ctx: VerCtx) -> int:
    rows = _old_rows(conn, "notes", link)
    return sum(
        _copy_row(conn, "notes", _write(_note_values(r, link, ctx), ctx)) for r in rows
    )


def _remap_one(conn, new: dict, ctx: VerCtx) -> int:
    old_key, score = _best_old(conn, new, ctx)
    if not old_key or score < knowledge_limits.REMAP_MIN_JACCARD:
        return 0
    status = knowledge_db.Status(
        new["id"],
        new["status"],
        {"remap_of": old_key},
        ctx.run_id,
        "remap@" + ctx.version,
    )
    knowledge_db.set_status(conn, "screens", status)
    link = Remap(old_key, new["screen_key"])
    return 1 + _copy_elements(conn, link, ctx) + _copy_notes(conn, link, ctx)


def remap_screens(conn, version: str, run_id: str) -> int:
    """Screens first seen in this run: copy a similar old screen's items, or nothing."""
    ctx = VerCtx(version, run_id)
    clause = "source_run = ? AND remap_of = '' AND invalid_at IS NULL"
    where = knowledge_db.Where(clause, (run_id,), 500)
    return sum(
        _remap_one(conn, new, ctx) for new in knowledge_db.rows(conn, "screens", where)
    )

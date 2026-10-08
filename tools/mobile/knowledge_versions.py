"""App-version bookkeeping for learned knowledge: flag, verify on use, remap, stale.

Nothing is ever deleted. On the first run of a NEW app version every active
element / shortcut / mistake / popup and screen-scoped note becomes
``needs_recheck`` (the prior status rides on the event's ``before_json``);
counters are kept and timings are left alone. An item confirmed in a run on that
version returns to its prior status; a run on a version seen before reactivates
what a newer version flagged. A missing element disables only its dependents for
that version. A screen key unseen before is matched to the previous version's
screens by anchor overlap and, at ``REMAP_MIN_JACCARD`` or more, its elements and
notes are copied as ``needs_recheck`` rows linked by ``supersedes``. Every write
goes through ``knowledge_db``; no function here raises past the stage.
"""

from __future__ import annotations

import json

from tools.mobile import knowledge_db, knowledge_limits, screen_key

FLAG_TABLES = ("elements", "shortcuts", "mistakes", "popups", "notes")
_ACTIVE = "status = 'active' AND invalid_at IS NULL"
_FLAG_LIKE = ("recheck@%", "missing@%", "remap@%")


def _loads(raw: object, default: object) -> object:
    try:
        got = json.loads(str(raw or ""))
    except ValueError:
        return default
    return got if isinstance(got, type(default)) else default


def run_version(conn, run_id: str, cache: dict) -> str:
    """``runs.app_version`` of *run_id* (cached per call chain); ``""`` when unknown."""
    if run_id not in cache:
        row = conn.execute(
            "SELECT app_version FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        cache[run_id] = str(row[0] or "") if row else ""
    return cache[run_id]


def is_new_version(conn, run_id: str, version: str) -> bool:
    """True when no EARLIER run (by insertion order) was on *version*."""
    row = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE app_version = ? AND run_id != ? AND rowid < "
        "COALESCE((SELECT rowid FROM runs WHERE run_id = ?), 0)",
        (version, run_id, run_id),
    ).fetchone()
    return int(row[0]) == 0


def _belongs(conn, row: dict, version: str, cache: dict) -> bool:
    if row.get("app_version") == version:
        return True
    return (
        bool(row.get("source_run"))
        and run_version(conn, str(row["source_run"]), cache) == version
    )


def flag_event(conn, table: str, row_id: int) -> tuple:
    """``(detail, prior_status)`` of the latest flag event of a row, or ``("", "")``."""
    got = conn.execute(
        "SELECT detail, before_json FROM events WHERE table_name = ? AND row_id = ? AND "
        "(detail LIKE ? OR detail LIKE ? OR detail LIKE ?) ORDER BY id DESC LIMIT 1",
        (table, row_id) + _FLAG_LIKE,
    ).fetchone()
    if got is None:
        return "", ""
    before = _loads(got[1], {})
    return str(got[0] or ""), str(before.get("status") or "active")


def _where(table: str) -> str:
    return _ACTIVE + (" AND COALESCE(screen_key, '') != ''" if table == "notes" else "")


def flag_version(conn, version: str, run_id: str) -> int:
    """Mark every active item not learned on *version* ``needs_recheck``."""
    done, cache = 0, {}
    for table in FLAG_TABLES:
        for row in knowledge_db.rows(
            conn, table, _where(table), (), knowledge_limits.SNAPSHOT_MAX_ROWS
        ):
            if _belongs(conn, row, version, cache):
                continue
            why = "recheck@" + version
            ok = knowledge_db.set_status(
                conn,
                table,
                row["id"],
                "needs_recheck",
                run_id=run_id,
                why=why,
                recheck_at=version,
            )
            done += int(ok)
    return done


def _restore(conn, table: str, row: dict, version: str, run_id: str) -> bool:
    detail, prior = flag_event(conn, table, row["id"])
    if not detail.endswith(version) and not detail.startswith("missing@" + version):
        return False
    if row.get("status") != "needs_recheck":
        return False
    return knowledge_db.set_status(
        conn,
        table,
        row["id"],
        prior or "active",
        run_id=run_id,
        why="verified@" + version,
        recheck_at="",
        app_version=version,
    )


def _rid_of(row: dict) -> str:
    return str(_loads(row.get("fp_json"), {}).get("rid") or "")


def _ok_rids(steps: list) -> set:
    done = ("ok", "assert_pass", "visual_check", "done")
    return {
        (s.get("screen_key"), s.get("target_rid"))
        for s in steps
        if s.get("outcome") in done and s.get("target_rid")
    }


def _touched(conn, table: str, run_id: str, cache: dict) -> list:
    """Flagged rows whose upvote list names this run (counters go through knowledge_db)."""
    out = []
    for row in knowledge_db.rows(
        conn, table, "status = 'needs_recheck' AND invalid_at IS NULL", (), 500
    ):
        if run_id in _loads(row.get("runs_json"), []):
            out.append(row)
    return out


def restore_confirmed(conn, steps: list, version: str, run_id: str) -> int:
    """Items confirmed in this run on *version* return to their prior status."""
    done = 0
    seen = _ok_rids(steps)
    fired = {
        int(f["note_id"])
        for s in steps
        for f in s.get("fired") or []
        if _num(f, "note_id")
    }
    for row in knowledge_db.rows(
        conn, "elements", "status = 'needs_recheck' AND invalid_at IS NULL", (), 500
    ):
        if (row.get("screen_key"), _rid_of(row)) in seen:
            done += int(_restore(conn, "elements", row, version, run_id))
    for row in knowledge_db.rows(
        conn, "notes", "status = 'needs_recheck' AND invalid_at IS NULL", (), 500
    ):
        if row["id"] in fired:
            done += int(_restore(conn, "notes", row, version, run_id))
    for table in ("mistakes", "popups", "shortcuts"):
        for row in _touched(conn, table, run_id, {}):
            done += int(_restore(conn, table, row, version, run_id))
    return done


def _num(item: object, key: str) -> bool:
    try:
        return isinstance(item, dict) and int(item.get(key)) >= 0
    except (TypeError, ValueError, OverflowError):
        return False


def reactivate_downgrade(conn, version: str, run_id: str) -> int:
    """A run on a version seen before: rows that belong to *version* and were
    flagged for another version go back to their prior status."""
    done, cache = 0, {}
    for table in FLAG_TABLES:
        for row in knowledge_db.rows(
            conn, table, "status = 'needs_recheck' AND invalid_at IS NULL", (), 500
        ):
            detail, prior = flag_event(conn, table, row["id"])
            if not detail.startswith("recheck@") or detail.endswith(version):
                continue
            if _belongs(conn, row, version, cache):
                ok = knowledge_db.set_status(
                    conn,
                    table,
                    row["id"],
                    prior or "active",
                    run_id=run_id,
                    why="downgrade@" + version,
                    recheck_at="",
                )
                done += int(ok)
    return done


def _disable(conn, table: str, row: dict, why: str, run_id: str) -> int:
    cols = {"recheck_at": why.split("@")[1].split(":")[0]}
    return int(
        knowledge_db.set_status(
            conn, table, row["id"], "needs_recheck", run_id=run_id, why=why, **cols
        )
    )


def _shortcut_uses(row: dict, rid: str) -> bool:
    for step in _loads(row.get("steps_json"), []):
        target = step.get("target") if isinstance(step, dict) else None
        if isinstance(step, dict) and rid in (
            step.get("rid"),
            (target or {}).get("rid") if isinstance(target, dict) else None,
        ):
            return True
    return False


def disable_dependents(conn, key: str, rid: str, version: str, run_id: str) -> int:
    """An element missing on *key*: disable its notes and shortcuts for *version*."""
    why = "missing@%s:%s" % (version, rid)
    done = 0
    for row in knowledge_db.rows(
        conn, "notes", _ACTIVE + " AND screen_key = ?", (key,), 500
    ):
        if str(_loads(row.get("when_json"), {}).get("rid") or "") == rid:
            done += _disable(conn, "notes", row, why, run_id)
    for row in knowledge_db.rows(conn, "shortcuts", _ACTIVE, (), 500):
        if _shortcut_uses(row, rid):
            done += _disable(conn, "shortcuts", row, why, run_id)
    return done


def _mark_missing(conn, key: str, rid: str, version: str, run_id: str) -> None:
    for row in knowledge_db.rows(
        conn, "elements", "screen_key = ? AND invalid_at IS NULL", (key,), 500
    ):
        if _rid_of(row) == rid and row.get("missing_at") != version:
            knowledge_db.set_status(
                conn,
                "elements",
                row["id"],
                row["status"],
                run_id=run_id,
                why="missing@%s:%s" % (version, rid),
                missing_at=version,
            )


def apply_missing(conn, steps: list, version: str, run_id: str) -> int:
    """Elements the queue and the heal could not find (outcome ``missing_element``)."""
    done, seen = 0, set()
    for rec in steps:
        slot = (rec.get("screen_key"), rec.get("target_rid"))
        if rec.get("outcome") != "missing_element" or not slot[1] or slot in seen:
            continue
        if str(slot[0]).startswith("sk1d:"):
            continue
        seen.add(slot)
        _mark_missing(conn, slot[0], slot[1], version, run_id)
        done += disable_dependents(conn, slot[0], slot[1], version, run_id)
    return done


def _best_old(conn, anchors: list, version: str, key: str) -> tuple:
    best = ("", 0.0)
    for old in knowledge_db.rows(
        conn, "screens", "invalid_at IS NULL AND app_version != ?", (version,), 500
    ):
        if old["screen_key"] == key:
            continue
        score = screen_key.similarity(anchors, _loads(old.get("anchors_json"), []))
        if score > best[1]:
            best = (old["screen_key"], score)
    return best


def _copy_elements(conn, old_key: str, new_key: str, version: str, run_id: str) -> int:
    done = 0
    for row in knowledge_db.rows(
        conn, "elements", "screen_key = ? AND " + _ACTIVE, (old_key,), 500
    ):
        fp = _loads(row.get("fp_json"), {})
        values = {
            "screen_key": new_key,
            "fp_json": row["fp_json"],
            "locators_json": row["locators_json"],
            "fp_hash": screen_key.fp_hash(dict(fp, remap=new_key)),
            "bound_field": row["bound_field"],
            "status": "needs_recheck",
            "recheck_at": version,
            "supersedes": row["id"],
            "trust": row["trust"],
            "app_version": version,
        }
        done += int(
            knowledge_db.insert(
                conn, "elements", values, run_id=run_id, why="remap@" + version
            )
            is not None
        )
    return done


def _copy_notes(conn, old_key: str, new_key: str, version: str, run_id: str) -> int:
    done = 0
    for row in knowledge_db.rows(
        conn, "notes", "screen_key = ? AND " + _ACTIVE, (old_key,), 500
    ):
        values = {
            k: row[k]
            for k in (
                "kind",
                "text",
                "when_json",
                "then_json",
                "scope_key",
                "trust",
                "element_fp",
            )
        }
        values.update(
            screen_key=new_key,
            status="needs_recheck",
            recheck_at=version,
            supersedes=row["id"],
            app_version=version,
        )
        done += int(
            knowledge_db.insert(
                conn, "notes", values, run_id=run_id, why="remap@" + version
            )
            is not None
        )
    return done


def remap_screens(conn, version: str, run_id: str) -> int:
    """Screens first seen in this run: copy a similar old screen's items, or nothing."""
    done = 0
    where = "source_run = ? AND remap_of = '' AND invalid_at IS NULL"
    for new in knowledge_db.rows(conn, "screens", where, (run_id,), 500):
        anchors = _loads(new.get("anchors_json"), [])
        old_key, score = _best_old(conn, anchors, version, new["screen_key"])
        if not old_key or score < knowledge_limits.REMAP_MIN_JACCARD:
            continue
        knowledge_db.set_status(
            conn,
            "screens",
            new["id"],
            new["status"],
            run_id=run_id,
            why="remap@" + version,
            remap_of=old_key,
        )
        done += 1 + _copy_elements(conn, old_key, new["screen_key"], version, run_id)
        done += _copy_notes(conn, old_key, new["screen_key"], version, run_id)
    return done


def stale_shortcuts(conn, run_id: str) -> int:
    """Shortcuts that failed too often (counters come from the shortcut plug)."""
    done = 0
    where = "status IN ('active', 'candidate') AND invalid_at IS NULL AND fail >= ? AND fail > success"
    for row in knowledge_db.rows(
        conn, "shortcuts", where, (knowledge_limits.STALE_AFTER_FAILS,), 500
    ):
        done += int(
            knowledge_db.set_status(
                conn,
                "shortcuts",
                row["id"],
                "stale",
                run_id=run_id,
                why="shortcut stale",
            )
        )
    return done


def recheck_total(conn) -> int:
    """Notes now waiting for a re-check (the report's "N notes need re-check")."""
    row = conn.execute(
        "SELECT COUNT(*) FROM notes WHERE status = 'needs_recheck' AND invalid_at IS NULL"
    ).fetchone()
    return int(row[0]) if row else 0

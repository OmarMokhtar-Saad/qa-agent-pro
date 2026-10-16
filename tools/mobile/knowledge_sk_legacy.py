"""Moving learning stored under an old ``sk1`` screen key to the ``sk2`` key.

``sk2`` folds the input structure into the hash, so every item learned under an
``sk1`` key stopped matching. Each learning run re-keys the active ``sk1`` rows
of a screen that now has an ``sk2`` row (same package, activity and anchors) with
``knowledge_db.supersede``, so nothing is lost and the history stays. A row whose
new natural key already exists is a duplicate and is closed as superseded. An
``sk1`` key with several ``sk2`` screens is not moved (read fallback only) and
goes ``stale`` ``SK1_RETIRE_DAYS`` after the package's first migration. Rows no
``sk2`` screen matches, and other packages' rows, are never touched.
"""

from __future__ import annotations

import time

from tools.mobile import knowledge_db, knowledge_schema_ddl
from tools.mobile import knowledge_sk_targets as sk_targets

#: Days an unclaimed ``sk1:`` row stays usable before it goes ``stale``.
SK1_RETIRE_DAYS = 30
_ROWS = 2000
_MARK = "sk1_eligible"
KEY_COLS = {
    "screens": ("screen_key",),
    "elements": ("screen_key",),
    "mistakes": ("screen_key",),
    "timings": ("screen_key",),
    "lessons": ("screen_key",),
    "notes": ("scope_key",),
    "edges": ("from_key", "to_key"),
    "shortcuts": ("start_key", "end_key"),
}
_LIVE = "invalid_at IS NULL AND status IN ('active', 'candidate', 'needs_recheck')"


def _taken(conn, table: str, values: dict) -> bool:
    cols = knowledge_schema_ddl.NATURAL_KEYS.get(table, ())
    if not cols:
        return False
    clause = " AND ".join("%s = ?" % c for c in cols)
    clause += " AND invalid_at IS NULL AND id != ?"
    hit = conn.execute(
        "SELECT 1 FROM %s WHERE %s LIMIT 1" % (table, clause),
        [values.get(c) for c in cols] + [values.get("id")],
    ).fetchone()
    return hit is not None


def _move_row(conn, row: dict, rep: tuple) -> int:
    table, old_key, new_key, run_id = rep
    values = {
        k: (new_key if v == old_key and k in KEY_COLS[table] else v)
        for k, v in row.items()
    }
    if _taken(conn, table, values):
        status = knowledge_db.Status(row["id"], "superseded", {}, run_id, "sk1->sk2")
        return int(knowledge_db.set_status(conn, table, status))
    rep_row = knowledge_db.Replace(row["id"], values, run_id, "sk1->sk2")
    return int(knowledge_db.supersede(conn, table, rep_row) is not None)


def move_table(conn, rep: tuple) -> int:
    table, done = rep[0], 0
    for col in KEY_COLS[table]:
        where = knowledge_db.Where(col + " = ? AND " + _LIVE, (rep[1],), _ROWS)
        for row in knowledge_db.rows(conn, table, where):
            done += _move_row(conn, row, rep)
    return done


def _eligible_since(conn, package: str) -> float:
    """When the package's ``sk1`` rows first became migratable (0.0 = never)."""
    sql = "SELECT MIN(ts) FROM events WHERE event = ? AND detail = ?"
    got = conn.execute(sql, (_MARK, package)).fetchone()
    return float(got[0] or 0.0)


def _mark_eligible(conn, package: str, run_id: str) -> None:
    if not _eligible_since(conn, package):
        ev = knowledge_db.Ev("", None, run_id, package, None)
        knowledge_db.event(conn, _MARK, ev)


def migrate(conn, package: str, run_id: str) -> int:
    """Re-key each live ``sk1`` row whose screen has exactly one ``sk2`` key."""
    found = sk_targets.candidates(conn, package)
    if found:
        _mark_eligible(conn, package, run_id)
    done = 0
    for old_key, news in found.items():
        if len(news) != 1:
            continue
        for table in KEY_COLS:
            done += move_table(conn, (table, old_key, next(iter(news)), run_id))
    return done


def _retire_key(conn, key: str, run_id: str) -> int:
    done = 0
    for table, cols in KEY_COLS.items():
        for col in cols:
            where = knowledge_db.Where(col + " = ? AND " + _LIVE, (key,), _ROWS)
            for row in knowledge_db.rows(conn, table, where):
                st = knowledge_db.Status(row["id"], "stale", {}, run_id, "sk1 aged")
                done += int(knowledge_db.set_status(conn, table, st))
    return done


def retire_stale(conn, package: str, run_id: str) -> int:
    """Close out the package's ``sk1:`` rows still left (several ``sk2``
    matches) ``SK1_RETIRE_DAYS`` after its first migration. Unmatched rows and
    other packages are untouched."""
    since = _eligible_since(conn, package)
    if not since or since > time.time() - SK1_RETIRE_DAYS * 86400:
        return 0
    keys = sk_targets.candidates(conn, package)
    return sum(_retire_key(conn, key, run_id) for key in keys)

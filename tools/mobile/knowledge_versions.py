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

from tools.mobile import knowledge_db, knowledge_limits
from tools.mobile.knowledge_versions_core import (  # noqa: F401
    FLAG_TABLES,
    VerCtx,
    flag_event,
    is_new_version,
    run_version,
)
from tools.mobile.knowledge_versions_flag import (  # noqa: F401
    flag_version,
    reactivate_downgrade,
    restore_confirmed,
)
from tools.mobile.knowledge_versions_missing import (  # noqa: F401
    apply_missing,
    disable_dependents,
)
from tools.mobile.knowledge_versions_remap import remap_screens  # noqa: F401


def stale_shortcuts(conn, run_id: str) -> int:
    """Shortcuts that failed too often (counters come from the shortcut plug)."""
    done = 0
    where = knowledge_db.Where(
        "status IN ('active', 'candidate') AND invalid_at IS NULL "
        "AND fail >= ? AND fail > success",
        (knowledge_limits.STALE_AFTER_FAILS,),
        500,
    )
    for row in knowledge_db.rows(conn, "shortcuts", where):
        status = knowledge_db.Status(
            row["id"], "stale", None, run_id=run_id, why="shortcut stale"
        )
        done += int(knowledge_db.set_status(conn, "shortcuts", status))
    return done


def recheck_total(conn) -> int:
    """Notes now waiting for a re-check (the report's "N notes need re-check")."""
    row = conn.execute(
        "SELECT COUNT(*) FROM notes WHERE status = 'needs_recheck' AND invalid_at IS NULL"
    ).fetchone()
    return int(row[0]) if row else 0

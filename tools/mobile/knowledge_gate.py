"""Stage C: the ONLY place a learned candidate is promoted or demoted (contract W2d).

``gate(conn, package, run_id)`` applies, in order:

1. feedback of this run: a TESTER note is flagged ``disputed`` (never changed); a
   learned item gets ``contradicted`` counters (a context-bound observation makes a
   variant instead);
2. promotion: tester trust is active at once; a candidate becomes active at its
   table's distinct-run threshold;
3. contradiction beats confirmation: a learned item with ``contradicted`` over
   ``confirmed`` (and the floor) is superseded by the proposal, or goes stale;
4. retention decay of lessons; shortcuts that failed too often go stale.

Every write goes through ``knowledge_db`` with the run id (rollback restores it).
The decay and feedback steps are guarded by ``learn_marker`` events, so running the
gate again for one run (after a reflection) changes nothing twice. No model call.
Never raises: a failure returns what was done so far.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_feedback
from tools.mobile import knowledge_limits as limits

logger = logging.getLogger(__name__)

#: table -> distinct runs a candidate needs (timings, edges, elements and screens are
#: gated by their own stages).
MIN_RUNS = {
    "notes": limits.GATE_MIN_RUNS,
    "lessons": limits.GATE_MIN_RUNS,
    "popups": limits.POPUP_MIN_RUNS,
    "mistakes": limits.MISTAKE_ACTIVE_RUNS,
    "shortcuts": limits.SHORTCUT_MIN_RUNS,
}
#: table -> the column a proposal replaces (tables without one go stale instead).
TEXT_COLUMN = {"notes": "text", "lessons": "text", "mistakes": "fix"}
_KEEP_OUT = (
    "id",
    "created",
    "valid_from",
    "invalid_at",
    "status",
    "confirmed",
    "contradicted",
    "supersedes",
    "runs_json",
    "trust",
    "source_run",
    "proposal_json",
    "variant_of",
)
_ERRORS = (sqlite3.Error, TypeError, ValueError, KeyError)


@dataclass
class Transition:
    table: str
    row_id: int
    old: str
    new: str
    why: str = ""


def runs_of(row: object) -> int:
    """Distinct runs that upvoted *row*."""
    try:
        return len(
            {r for r in json.loads(str(row["runs_json"] or "[]")) if isinstance(r, str)}
        )
    except (ValueError, TypeError, KeyError, IndexError):
        return 0


def _row(conn: object, table: str, row_id: int):
    return conn.execute("SELECT * FROM %s WHERE id = ?" % table, (row_id,)).fetchone()


def _open(row: object) -> bool:
    return row is not None and row["invalid_at"] is None


def _variant_values(table: str, old: object, text: str, env: dict) -> dict:
    """The new row for a context-bound proposal: the old row's keys, the new text."""
    values = {k: old[k] for k in old.keys() if k not in _KEEP_OUT}
    column = TEXT_COLUMN.get(table)
    if column:
        values[column] = text
    if table == "notes":
        when = json.loads(old["when_json"] or "{}")
        when["env"] = {**(when.get("env") or {}), **env}
        values["when_json"] = json.dumps(when, sort_keys=True)
        values["scope_key"] = "%s|%s" % (old["kind"], json.dumps(when, sort_keys=True))
        values["variant_of"] = old["id"]
    elif table == "lessons":
        values["text"] = "(%s) %s" % (
            ", ".join("%s=%s" % kv for kv in env.items()),
            text,
        )
    return values


def _insert_variant(
    conn: object, table: str, old: object, fb: dict, run_id: str
) -> int | None:
    env = knowledge_feedback.context_of(fb.get("observation"))
    text = str(fb.get("proposed") or "").strip()
    if not env or not text or table not in TEXT_COLUMN:
        return None
    values = _variant_values(table, old, text, env)
    values.update(status="candidate", trust="host")
    return kdb.insert(
        conn, table, values, run_id=run_id, why="variant of %s" % old["id"]
    )


def _dispute_tester_note(
    conn: object, row: object, fb: dict, run_id: str
) -> Transition | None:
    """A tester note is never auto-changed. An avoid note keeps guarding (it stays
    active, flagged by its proposal); any other kind becomes ``disputed``."""
    proposal = json.dumps(
        {
            "proposed": fb.get("proposed", ""),
            "observation": fb.get("observation", ""),
            "env": knowledge_feedback.context_of(fb.get("observation")),
            "run_id": run_id,
        },
        sort_keys=True,
    )
    status = row["status"] if row["kind"] == "avoid" else "disputed"
    if row["status"] == status and row["proposal_json"] == proposal:
        return None
    if kdb.set_status(
        conn,
        "notes",
        row["id"],
        status,
        proposal_json=proposal,
        run_id=run_id,
        why="disputed",
    ):
        return Transition(
            "notes", row["id"], row["status"], status, "tester note disputed"
        )
    return None


def _count_contradiction(
    conn: object, table: str, row: object, fb: dict, run_id: str
) -> Transition | None:
    """A learned item: evidence counters (a half-weight report needs a second one)."""
    if _insert_variant(conn, table, row, fb, run_id) is not None:
        return Transition(
            table, row["id"], row["status"], row["status"], "variant added"
        )
    delta = 1 if float(fb.get("weight") or 0) >= 1.0 else 0
    if delta <= 0:
        return None
    done = kdb.set_status(
        conn,
        table,
        row["id"],
        row["status"],
        run_id=run_id,
        why="feedback",
        runs_json=row["runs_json"] or "[]",
        contradicted=int(row["contradicted"] or 0) + delta,
    )
    return (
        Transition(table, row["id"], row["status"], row["status"], "contradicted")
        if done
        else None
    )


def _feedback(conn: object, run_id: str) -> list:
    done: list = []
    for fb in knowledge_feedback.run_feedback(conn, run_id):
        parsed = kdb.parse_item_id(fb.get("item_id"))
        if parsed is None:
            continue
        table, row_id = parsed
        row = _row(conn, table, row_id)
        if not _open(row):
            continue
        try:
            if table == "notes" and row["trust"] == "tester":
                got = _dispute_tester_note(conn, row, fb, run_id)
            else:
                got = _count_contradiction(conn, table, row, fb, run_id)
        except _ERRORS:
            logger.exception("knowledge gate: feedback on %s failed", fb.get("item_id"))
            continue
        if got:
            done.append(got)
    return done


def _promote(conn: object, run_id: str, table: str) -> list:
    done: list = []
    need = MIN_RUNS[table]
    rows = conn.execute(
        "SELECT * FROM %s WHERE status = 'candidate' AND invalid_at IS NULL ORDER BY id"
        % table
    ).fetchall()
    for row in rows:
        if row["trust"] == "tester":
            why = "tester trust"
        elif int(row["contradicted"] or 0) > int(row["confirmed"] or 0):
            continue
        elif table == "shortcuts" and int(row["fail"] or 0) > 0:
            continue
        elif runs_of(row) >= need:
            why = "%d distinct runs" % runs_of(row)
        else:
            continue
        if kdb.set_status(conn, table, row["id"], "active", run_id=run_id, why=why):
            done.append(Transition(table, row["id"], "candidate", "active", why))
    return done


def _resolve_contradicted(conn: object, run_id: str, table: str) -> list:
    done: list = []
    rows = conn.execute(
        "SELECT * FROM %s WHERE status = 'active' AND invalid_at IS NULL AND trust != 'tester'"
        " AND contradicted >= ? AND contradicted > confirmed ORDER BY id" % table,
        (limits.CONTRADICT_FLOOR,),
    ).fetchall()
    for row in rows:
        item = "%s:%d" % (table, row["id"])
        proposal = knowledge_feedback.latest_proposal(conn, item)
        column = TEXT_COLUMN.get(table)
        if column and proposal.get("proposed"):
            values = {k: row[k] for k in row.keys() if k not in _KEEP_OUT}
            values.update(
                {column: proposal["proposed"], "status": "candidate", "trust": "host"}
            )
            new = kdb.supersede(
                conn, table, row["id"], values, run_id=run_id, why="contradicted"
            )
            if new is not None:
                done.append(
                    Transition(
                        table,
                        row["id"],
                        "active",
                        "superseded",
                        "replaced by #%d" % new,
                    )
                )
                continue
        if kdb.set_status(
            conn, table, row["id"], "stale", run_id=run_id, why="contradicted"
        ):
            done.append(
                Transition(
                    table, row["id"], "active", "stale", "contradicted, no proposal"
                )
            )
    return done


def _decay(conn: object, run_id: str, seen: set) -> list:
    """Lessons: used this run (or global) reset; unused decay, stale under the floor."""
    done: list = []
    rows = conn.execute(
        "SELECT * FROM lessons WHERE status = 'active' AND invalid_at IS NULL ORDER BY id"
    ).fetchall()
    for row in rows:
        if not row["screen_key"] or row["screen_key"] in seen:
            kdb.set_status(
                conn,
                "lessons",
                row["id"],
                "active",
                retention=1.0,
                last_used_run=run_id,
                run_id=run_id,
                why="used",
            )
            continue
        value = (
            float(row["retention"] if row["retention"] is not None else 1.0)
            * limits.RETENTION_DECAY
        )
        status = "stale" if value < limits.STALE_RETENTION else "active"
        if (
            kdb.set_status(
                conn,
                "lessons",
                row["id"],
                status,
                retention=value,
                run_id=run_id,
                why="unused",
            )
            and status == "stale"
        ):
            done.append(
                Transition(
                    "lessons", row["id"], "active", "stale", "unused for too long"
                )
            )
    return done


def _failing_shortcuts(conn: object, run_id: str) -> list:
    done: list = []
    rows = conn.execute(
        "SELECT id FROM shortcuts WHERE status IN ('active', 'candidate') AND invalid_at IS NULL AND fail >= ?",
        (limits.STALE_AFTER_FAILS,),
    ).fetchall()
    for row in rows:
        if kdb.set_status(
            conn, "shortcuts", row["id"], "stale", run_id=run_id, why="failed too often"
        ):
            done.append(
                Transition(
                    "shortcuts", row["id"], "active", "stale", "failed too often"
                )
            )
    return done


def _once(conn: object, run_id: str, name: str) -> bool:
    """True the first time *name* runs for *run_id* (and records it)."""
    if kdb.has_marker(conn, run_id, name):
        return False
    kdb.put_marker(conn, run_id, name)
    return True


def gate(conn: object, package: str, run_id: str, steps: list | None = None) -> list:
    """Run the gate for one run; the transitions made. Never raises."""
    out: list = []
    try:
        if steps is None:
            from tools.mobile import knowledge_trace

            steps = knowledge_trace.load_steps(run_id)
        if _once(conn, run_id, "gate_feedback"):
            out += _feedback(conn, run_id)
        for table in MIN_RUNS:
            out += _promote(conn, run_id, table)
        for table in MIN_RUNS:
            out += _resolve_contradicted(conn, run_id, table)
        out += _failing_shortcuts(conn, run_id)
        if steps and _once(conn, run_id, "gate_decay"):
            out += _decay(conn, run_id, {str(s.get("screen_key") or "") for s in steps})
    except _ERRORS:
        logger.exception("knowledge gate failed for %s", str(run_id)[:64])
    return out

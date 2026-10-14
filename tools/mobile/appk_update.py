"""Updating notes: retire one, flush a replay's counters and events."""

from __future__ import annotations

import time
from contextlib import closing
from typing import Any, NamedTuple

from config.settings_guard import never_raise
from tools.mobile import appk_conn
from tools.mobile.appk_paths import _store_path
from tools.mobile.appk_reply import _err, _fail, _ok
from tools.mobile.appk_rows import Event, _add_event
from tools.mobile.appk_secret import _secret_reason
from tools.mobile.appk_when import _clean


class Retirement(NamedTuple):
    """Why a note is retired and by which run."""

    reason: str = ""
    run_id: str = ""


class Tallies(NamedTuple):
    """A replay's buffered counts and events, and its run id."""

    counts: Any
    events: Any
    run_id: str = ""


def connect(path: Any, *, write: bool) -> Any:
    """The store connection; named so a test can replace it."""
    return appk_conn._connect(path, write=write)


def _retire_row(path: Any, number: int, tail: tuple) -> dict:
    why, run_id = tail
    with closing(connect(path, write=True)) as conn, conn:
        # Lock before the status read, as add_note does: two racing
        # retires must not both see the note active.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM notes WHERE id = ?", (number,)
        ).fetchone()
        if row is None:
            return _err("no note #%d" % number)
        if row["status"] != "active":
            return _err("note #%d is already %s" % (number, row["status"]))
        conn.execute(
            "UPDATE notes SET status = 'retired', invalid_at = ? WHERE id = ?",
            (time.time(), number),
        )
        _add_event(conn, number, Event("retired", why, run_id))
    return _ok({"id": number, "status": "retired"})


# Retire one active note. The row stays: retire is a status, not a delete.
def retire_note(package: Any, note_id: Any, why: Retirement = Retirement()) -> dict:
    with never_raise() as g:
        path = _store_path(package)
        if path is None:
            return _err("that is not an Android package name")
        try:
            number = int(note_id)
        except (TypeError, ValueError, OverflowError):
            return _err("note_id must be a whole number")
        if not path.exists():
            return _err("no notes saved for this app")
        text, _ = _clean(str(why.reason or ""), "reason")
        if _secret_reason(text, (), why.run_id):
            text = ""
        return _retire_row(path, number, (text, why.run_id))
    return _fail("retire_note", g.exc)


def _apply_tallies(conn, counts: Any) -> int:
    counted = 0
    for note_id, tally in dict(counts or {}).items():
        cur = conn.execute(
            "UPDATE notes SET confirmed = confirmed + ?, "
            "contradicted = contradicted + ? "
            "WHERE id = ? AND status = 'active'",
            (
                int(tally.get("confirmed", 0)),
                int(tally.get("contradicted", 0)),
                int(note_id),
            ),
        )
        counted += cur.rowcount
    return counted


def _log_events(conn, events: Any, run_id: str) -> int:
    logged = 0
    for item in list(events or []):
        event = Event(str(item.get("event", "")), item.get("detail", ""), run_id)
        if _add_event(conn, item.get("note_id"), event):
            logged += 1
    return logged


# Write a replay's buffered tallies. Counts only notes that are STILL
# active; events stop at ``MAX_EVENT_ROWS``. A missing store is a no-op.
def flush_counters(package: Any, tallies: Tallies) -> dict:
    with never_raise() as g:
        path = _store_path(package)
        if path is None or not path.exists():
            return _ok({"counted": 0, "events": 0})
        with closing(connect(path, write=True)) as conn, conn:
            counted = _apply_tallies(conn, tallies.counts)
            logged = _log_events(conn, tallies.events, tallies.run_id)
        return _ok({"counted": counted, "events": logged})
    return _fail("flush_counters", g.exc)

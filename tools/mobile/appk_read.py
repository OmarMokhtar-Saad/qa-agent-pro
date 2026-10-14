"""Reading the knowledge store: notes and executor guards."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from config.settings_guard import never_raise
from tools.mobile import appk_consts
from tools.mobile.appk_conn import _connect
from tools.mobile.appk_paths import _store_path
from tools.mobile.appk_reply import _err, _fail, _ok
from tools.mobile.appk_rows import _note


def _read_notes(package: Any, where: str) -> tuple:
    """``(notes, error)``. A missing store is ``([], None)`` and is NOT created.
    *where* is a constant SQL fragment, never caller text."""
    path = _store_path(package)
    if path is None:
        return [], "that is not an Android package name"
    if not path.exists():
        return [], None
    with closing(_connect(path, write=False)) as conn:
        rows = conn.execute(
            "SELECT * FROM notes " + where + " ORDER BY id DESC LIMIT ?",
            (
                appk_consts.MAX_LIST_ROWS
                if where == ""
                else appk_consts.MAX_ACTIVE_NOTES,
            ),
        ).fetchall()
    return [_note(row) for row in reversed(rows)], None


def list_notes(package: Any, *, include_inactive: bool = False) -> dict:
    """The app's notes, oldest first. Read-only; a missing store is ``[]``."""
    with never_raise() as g:
        notes, problem = _read_notes(
            package, "" if include_inactive else "WHERE status = 'active'"
        )
        return _err(problem) if problem else _ok(notes)
    return _fail("list_notes", g.exc)


def load_guards(package: Any) -> dict:
    """Every ACTIVE note, for the executor. Read-only; creates nothing."""
    with never_raise() as g:
        notes, problem = _read_notes(
            package, "WHERE status IN ('active', 'needs_recheck')"
        )
        return _err(problem) if problem else _ok(notes)
    return _fail("load_guards", g.exc)

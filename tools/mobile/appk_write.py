"""Writing a note: validation, secret refusal, supersede and insert."""

from __future__ import annotations

import json
import time
from contextlib import closing
from pathlib import Path
from typing import Any, NamedTuple

from config.settings_guard import never_raise
from tools.mobile.appk_conn import _connect
from tools.mobile.appk_paths import _store_path
from tools.mobile.appk_reply import _err, _fail, _ok
from tools.mobile.appk_rows import Event, _add_event, _cap_error, _insert_note
from tools.mobile.appk_secret import _leaf_strings, _secret_reason
from tools.mobile.appk_validate import _validate


class NoteDraft(NamedTuple):
    """The note as the caller wrote it."""

    text: Any
    kind: Any
    when: Any = None
    then: Any = None


class NoteMeta(NamedTuple):
    """Where a note came from: its run, the secrets to refuse, the app version."""

    source_run: str = ""
    secrets: Any = ()
    app_version: str = ""


def _plaintext_refusal(clean: tuple, held: tuple) -> str:
    """The refusal text when the note holds a secret, else ``""``. *clean* is
    ``(text, when, then)``, *held* is ``(secrets, source_run)``."""
    text, when, then = clean
    secrets, source_run = held
    blob = "\n".join([text] + _leaf_strings(when) + _leaf_strings(then))
    reason = _secret_reason(blob, secrets, source_run)
    if not reason:
        return ""
    return (
        "%s. Notes are stored in plain text on this machine: describe "
        "the screen or the timing, never a password, a code or a "
        "personal number" % reason
    )


def _checked(package: Any, parts: tuple, held: tuple) -> Any:
    """An error dict, or ``(path, (text, when, then))`` for a note to write."""
    path = _store_path(package)
    if path is None:
        return _err("that is not an Android package name")
    clean = _validate(parts)
    if isinstance(clean, str):
        return _err(clean)
    reason = _plaintext_refusal(clean, held)
    return _err(reason) if reason else (path, clean)


def _scope(kind: Any, when: Any) -> str:
    return str(kind) + "|" + json.dumps(when, sort_keys=True)


def _active_ids(conn, scope: str) -> list:
    return [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM notes WHERE scope_key = ? AND status = 'active'",
            (scope,),
        )
    ]


def _note_values(note: tuple, meta: tuple, link: tuple) -> tuple:
    """The insert tuple; *link* is ``(scope, superseded ids, now)``."""
    kind, text, when, then = note
    app_version, source_run = meta
    scope, old, now = link
    return (
        kind,
        text,
        json.dumps(when, sort_keys=True),
        json.dumps(then, sort_keys=True),
        scope,
        str(app_version or "")[:64],
        str(source_run or "")[:64],
        old[0] if old else None,
        now,
    )


# Store a validated *note* ``(kind, text, when, then)``.
#
# *meta* is ``(app_version, source_run)``. A same-scope active note is
# marked superseded, never deleted.
def _write_note(path: Path, note: tuple, meta: tuple) -> dict:
    kind, text, when, then = note
    scope = _scope(kind, when)
    now = time.time()
    with closing(_connect(path, write=True)) as conn, conn:
        # Take the write lock BEFORE the scope/count reads: legacy
        # sqlite3 isolation opens a transaction only at the first write,
        # so two runs could both pass the cap check.
        conn.execute("BEGIN IMMEDIATE")
        old = _active_ids(conn, scope)
        full = _cap_error(conn, len(old))
        if full:
            return _err(full)
        values = _note_values(note, meta, (scope, old, now))
        new_id = _insert_note(conn, values, old)
        _add_event(conn, new_id, Event("added", kind, meta[1]))
    return _ok(
        {"id": new_id, "kind": kind, "text": text, "when": when, "superseded": old}
    )


# Save a note. ``content`` is ``{id, kind, text, superseded: [ids]}``.
#
# Validation and the secret check run BEFORE anything is opened, so a refusal
# creates no directory. A note with the same scope (kind plus ``when``)
# SUPERSEDES the old one: the old row is marked, never deleted.
def add_note(package: Any, draft: NoteDraft, meta: NoteMeta = NoteMeta()) -> dict:
    with never_raise() as g:
        got = _checked(package, tuple(draft), (meta.secrets, meta.source_run))
        if isinstance(got, dict):
            return got
        path, clean = got
        return _write_note(
            path, (draft.kind, *clean), (meta.app_version, meta.source_run)
        )
    return _fail("add_note", g.exc)

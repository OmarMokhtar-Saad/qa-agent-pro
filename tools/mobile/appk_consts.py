"""Caps, kinds and shared patterns of the per-app knowledge store."""

from __future__ import annotations

import re

from tools.mobile import knowledge_limits

#: Notes that may be ACTIVE for one app. Each is a guard read on every replay.
MAX_ACTIVE_NOTES = 100
#: Notes kept per app, history included: supersede and retire delete nothing,
#: so this stops the file growing; at the cap the oldest retired or superseded
#: rows are dropped to make room (an active note never is).
MAX_NOTE_ROWS = 1000
#: Characters in one note's text. It is echoed to the host model.
MAX_NOTE_CHARS = 600
#: Counter events kept per app. Past it the counters still count.
MAX_EVENT_ROWS = 5000
#: Notes one list reply carries.
MAX_LIST_ROWS = 50
#: Seconds one sqlite call may wait on a locked file.
DB_TIMEOUT_S = 2.0

#: A wait note that names no ``ms`` waits this long at most.
DEFAULT_GUARD_WAIT_MS = 5000
#: A held tester value shorter than this is not searched for in a note.
HELD_VALUE_LENGTH = 3
KINDS = ("wait", "avoid", "precondition", "fact")
#: Kinds the executor reads as guards; the others are context only.
GUARD_KINDS = ("wait", "avoid")

_SCHEMA_VERSION = "1"
_FIELD_CHARS = 200
_ENV_ENTRIES = knowledge_limits.ENV_ENTRIES
_WHEN_KEYS = ("op", "rid", "activity", "env")
_THEN_KEYS = ("until_rid", "until_text", "until_gone", "until_rid_text", "ms")
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
_OP_RE = re.compile(r"^[a-z_]{1,32}$")

"""Per-app knowledge: notes a tester saved about ONE Android app.

One SQLite file per package at ``<cache>/apps/<package>/knowledge.db`` (tables
``meta``, ``notes``, ``events``). It is local to this machine and never
uploaded. This module owns validation, secret refusal, the caps, supersede and
the counters. Callers: the ``note`` parameter and ``qa_mobile_notes`` (through
``handlers/``) and the executor's guard.

No public function raises: each returns ``{"error": ..., "content": ...}``.
A note is text the host model reads back, so it is only ever echoed through
``wrap_untrusted``; and a note can make a replay WAIT (bounded) or REFUSE an
op, never allow one the destructive guard stops.
"""

from __future__ import annotations

import hashlib  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import sqlite3  # noqa: F401
import time  # noqa: F401
from contextlib import closing  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from tools import api_redact, sqlite_store  # noqa: F401
from tools.mobile import (  # noqa: F401
    actions,
    held_inputs,
    knowledge_limits,
    knowledge_schema,
    paths,
)
from tools.mobile.appk_conn import (  # noqa: F401
    _SCHEMA,
    _connect,
    _create_store_files,
    _init_or_close,
    _init_schema,
    _migrate_once,
)
from tools.mobile.appk_consts import (  # noqa: F401
    _ENV_ENTRIES,
    _FIELD_CHARS,
    _OP_RE,
    _PACKAGE_RE,
    _SCHEMA_VERSION,
    _THEN_KEYS,
    _WHEN_KEYS,
    DB_TIMEOUT_S,
    DEFAULT_GUARD_WAIT_MS,
    GUARD_KINDS,
    HELD_VALUE_LENGTH,
    KINDS,
    MAX_ACTIVE_NOTES,
    MAX_EVENT_ROWS,
    MAX_LIST_ROWS,
    MAX_NOTE_CHARS,
    MAX_NOTE_ROWS,
)
from tools.mobile.appk_match import (  # noqa: F401
    GuardTarget,
    _env_applies,
    _guard_applies,
    activity_ok,
    candidates,
    env_matches,
    rid_matches,
)
from tools.mobile.appk_paths import (  # noqa: F401
    _dir_name,
    _store_path,
    _tighten,
    _tighten_store,
    db_path,
)
from tools.mobile.appk_read import (  # noqa: F401
    _read_notes,
    list_notes,
    load_guards,
)
from tools.mobile.appk_render import _render_line, echo, render  # noqa: F401
from tools.mobile.appk_reply import _err, _fail, _ok, logger  # noqa: F401
from tools.mobile.appk_rows import (  # noqa: F401
    Event,
    _add_event,
    _cap_error,
    _counts,
    _extra_keys,
    _insert_note,
    _load,
    _make_room,
    _note,
)
from tools.mobile.appk_secret import (  # noqa: F401
    _ALPHA_RE,
    _DIGITS4_RE,
    _DIGITS_RE,
    _NEAR_WORDS,
    _SPACED_DIGITS_RE,
    _SPLIT_RE,
    _credential_near_value,
    _is_value_token,
    _leaf_strings,
    _secret_reason,
    secret_reason,
)
from tools.mobile.appk_stated import (  # noqa: F401
    _ASSIGNED_RE,
    _CODE_SEP,
    _CODE_WORDS,
    _DATE_RE,
    _LINE_BREAK_RE,
    _MEASURE_GROUP_DIGITS,
    _MEASURE_MAX_DIGITS,
    _MORE_DIGITS_RE,
    _PROSE_WORD,
    _SPACE_SEP,
    _STATED_DIGITS_RE,
    _STATED_WORDS,
    _THOUSANDS_RE,
    _UNIT_AFTER_RE,
    _WORD_PREFIX,
    _is_date,
    _is_measurement,
    _states_digits,
)
from tools.mobile.appk_then import (  # noqa: F401
    _gone_and_ms,
    _until_fields,
    _until_gone,
    _validate_steps,
    _validate_then,
    _wait_ms,
)
from tools.mobile.appk_update import (  # noqa: F401
    Retirement,
    Tallies,
    _apply_tallies,
    _log_events,
    _retire_row,
    flush_counters,
    retire_note,
)
from tools.mobile.appk_validate import _validate, _validate_by_kind  # noqa: F401
from tools.mobile.appk_when import _clean, _validate_when  # noqa: F401
from tools.mobile.appk_write import (  # noqa: F401
    NoteDraft,
    NoteMeta,
    _active_ids,
    _checked,
    _note_values,
    _plaintext_refusal,
    _scope,
    _write_note,
    add_note,
)
from tools.untrusted import wrap_untrusted  # noqa: F401

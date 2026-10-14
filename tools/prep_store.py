"""Pending-generation store — SQLite-backed, never-raises (host-mode Phase 1).

Host-mode ("boomerang") test-case generation splits the pipeline across TWO
MCP tool calls: ``qa_prepare_test_cases`` builds a grounded prompt and hands it
to the tester's own chat model, then ``qa_submit_suite`` validates the JSON the
host produced. Because those are two separate stateless tool calls, the prepared
context (grounded prompt, checklist, category specs, case-count bounds, ticket
provenance) must persist BETWEEN them so submit validates against what was
actually prepared — and so a stale, unknown, or tampered ``prep_id`` is rejected
rather than trusted.

This module persists each prep record (and, for weaker hosts that submit one
category at a time, its incremental per-category submissions) to the SAME
SQLite file ``tools/suite_store.py`` uses, in its own tables. It mirrors
suite_store's never-raise contract exactly:

  On success: {"error": None, "content": <value>}
  On failure: {"error": str, "content": None}

All blocking sqlite I/O runs in ``asyncio.to_thread()``. Records carry a
creation timestamp and are expired on read (TTL, ``QA_PREP_TTL_S``). ``prep_id``
is an unguessable uuid4 hex, so an unknown/tampered id simply misses and load
returns ``None`` — the caller (``qa_submit_suite``) then refuses to finalize.
Payloads over ``QA_PREP_MAX_BYTES`` are rejected at save time so a host cannot
wedge the store.

Facade: the code lives in the ``tools/prep_store_*`` leaves; every name is
re-exported here so ``from tools import prep_store`` callers and patches work.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import json  # noqa: F401
import logging
import sqlite3  # noqa: F401
import time  # noqa: F401
import uuid  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from tools import sqlite_store  # noqa: F401
from tools.install_paths import resolve_data_path  # noqa: F401
from tools.prep_store_cfg import (  # noqa: F401
    _BUSY_TIMEOUT_S,
    _DEFAULT_MAX_BYTES,
    _DEFAULT_MAX_LIFETIME_S,
    _DEFAULT_TTL_S,
    _db_path,
    _expired,
    _expires_at,
    _max_bytes,
    _max_lifetime_s,
    _sliding_ttl_on,
    _touch_enabled,
    _ttl_seconds,
)
from tools.prep_store_conn import (  # noqa: F401
    _SCHEMA,
    FINALIZED_KEY,
    _alter_add_touched_at,
    _connect,
    _ensure_touched_at,
    _has_touched_at,
    _purge_expired_sync,
    is_finalized_record,
)
from tools.prep_store_crud import (  # noqa: F401
    _delete_prep_sync,
    _load_prep_sync,
    _save_prep_sync,
    _update_prep_sync,
    delete_prep,
    load_prep,
    save_prep,
    update_prep,
)
from tools.prep_store_find import (  # noqa: F401
    _clamped_count,
    _clean_strings,
    _find_prep_snapshot_sync,
    _find_recent_prep_sync,
    _image_state,
    find_prep_snapshot_by_source,
    find_recent_prep_by_source,
)
from tools.prep_store_list import (  # noqa: F401
    _expected_count,
    _list_unfinished_sync,
    _unfinished_rows,
    list_unfinished_preps,
)
from tools.prep_store_subs import (  # noqa: F401
    _load_submissions_sync,
    _save_submission_sync,
    load_submissions,
    save_submission,
)
from tools.prep_store_touch import _touch_prep_sync, touch_prep  # noqa: F401

logger = logging.getLogger("tools.prep_store")

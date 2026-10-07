"""Per-run knowledge snapshot and its bounded registry (contract 1.7).

``load`` is sync and is called from the executor's ``_read_knowledge`` inside its
existing ``to_thread``. The result is stored at ``ctx.knowledge["snap"]`` and,
through ``put``, in a bounded registry so ``agents/`` can reach it by run id
without importing the executor. Each owner fills only its own keys; Wave 1 ships
the empty shape. No function here raises.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections import OrderedDict

from tools.mobile import app_knowledge, knowledge_hooks
from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits

logger = logging.getLogger(__name__)

#: key -> the empty value of that key (dict keys are screen keys, lists are flat).
SNAPSHOT_KEYS = {
    "timings": dict,
    "elements": dict,
    "popups": list,
    "shortcuts": list,
    "edges": dict,
    "mistakes": dict,
    "lessons": dict,
    "global": list,
    "disputed": list,
}

_LOCK = threading.Lock()
_BY_RUN: OrderedDict = OrderedDict()


def empty() -> dict:
    return {key: make() for key, make in SNAPSHOT_KEYS.items()}


_LIVE = ("active", "needs_recheck")


def _tag(rows: list, table: str) -> list:
    return [dict(row, _table=table) for row in rows]


def _env_ok(row: dict, env: object) -> bool:
    try:
        when = json.loads(row.get("when_json") or "{}")
    except ValueError:
        return False
    return app_knowledge.env_matches((when or {}).get("env"), env)


def _fill(conn: object, facts: object, snap: dict) -> None:
    env = getattr(facts, "env", None) or {}
    cap = limits.SNAPSHOT_MAX_ROWS
    notes = kdb.rows(
        conn,
        "notes",
        "status IN ('active', 'needs_recheck', 'disputed') AND invalid_at IS NULL",
        (),
        limit=cap,
    )
    lessons = kdb.rows(
        conn, "lessons", "status = 'active' AND invalid_at IS NULL", (), limit=cap
    )
    for row in sorted(notes, key=lambda r: r["id"]):
        if row.get("proposal_json") or row["status"] == "disputed":
            snap["disputed"].append(_tag([row], "notes")[0])
        elif row["status"] in _LIVE and _env_ok(row, env):
            bucket = row.get("screen_key") or ""
            if bucket:
                snap["lessons"].setdefault(bucket, []).extend(_tag([row], "notes"))
            else:
                snap["global"].append(_tag([row], "notes")[0])
    for row in sorted(lessons, key=lambda r: r["id"]):
        bucket = row.get("screen_key") or ""
        if bucket:
            snap["lessons"].setdefault(bucket, []).extend(_tag([row], "lessons"))
        else:
            snap["global"].append(_tag([row], "lessons")[0])


def load(package: object, facts: object = None) -> dict:
    """The snapshot for *package*: the lessons, global and disputed keys filled here,
    the others merged from every plug's ``snapshot``. Read-only; never creates a store."""
    snap = empty()
    try:
        path = app_knowledge._store_path(package)
        if path is None or not path.exists():
            return snap
        conn = app_knowledge._connect(path, write=False)
        try:
            _fill(conn, facts, snap)
            for key, value in knowledge_hooks.snapshots(conn, facts).items():
                if key in SNAPSHOT_KEYS and key not in (
                    "lessons",
                    "global",
                    "disputed",
                ):
                    snap[key] = value
        finally:
            conn.close()
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError):
        logger.exception("knowledge snapshot load failed")
    return snap


def load_for_run(run_id: object) -> dict:
    """Load and register the snapshot of *run_id* (package and env from its manifest
    and run row). Always registers something, so a miss is not retried every turn."""
    snap = empty()
    try:
        from tools.mobile import knowledge_run, run_store

        content = run_store.read_manifest(str(run_id or "")).get("content") or {}
        package = str(content.get("package") or "")
        env: dict = {}
        path = app_knowledge._store_path(package) if package else None
        if path is not None and path.exists():
            conn = app_knowledge._connect(path, write=False)
            try:
                row = conn.execute(
                    "SELECT env_json FROM runs WHERE run_id = ?", (str(run_id),)
                ).fetchone()
                env = json.loads(row[0]) if row else {}
            finally:
                conn.close()
        facts = knowledge_run.RunFacts(
            run_id=str(run_id),
            package=package,
            env=env if isinstance(env, dict) else {},
        )
        snap = load(package, facts)
    except (
        sqlite3.Error,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OSError,
        ImportError,
    ):
        logger.exception("knowledge snapshot load_for_run failed")
    put(run_id, snap)
    return snap


def put(run_id: object, snap: object) -> None:
    """Register *snap* for *run_id*; the oldest run falls out past MAX_CACHED_RUNS."""
    try:
        key = str(run_id or "")
        if not key or not isinstance(snap, dict):
            return
        with _LOCK:
            _BY_RUN[key] = snap
            _BY_RUN.move_to_end(key)
            while len(_BY_RUN) > limits.MAX_CACHED_RUNS:
                _BY_RUN.popitem(last=False)
    except Exception:
        logger.exception("knowledge snapshot put failed")


def for_run(run_id: object) -> dict | None:
    try:
        with _LOCK:
            return _BY_RUN.get(str(run_id or ""))
    except Exception:
        logger.exception("knowledge snapshot lookup failed")
        return None


def drop(run_id: object) -> None:
    try:
        with _LOCK:
            _BY_RUN.pop(str(run_id or ""), None)
    except Exception:
        logger.exception("knowledge snapshot drop failed")

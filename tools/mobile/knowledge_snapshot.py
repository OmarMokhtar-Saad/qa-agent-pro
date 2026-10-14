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

from tools.mobile import app_knowledge, knowledge_hooks, knowledge_run, run_store
from tools.mobile import knowledge_limits as limits
from tools.mobile.knowledge_snapshot_fill import fill

logger = logging.getLogger(__name__)

#: key -> the empty value of that key (dict keys are screen keys, lists are flat).
SNAPSHOT_KEYS = {
    "timings": dict,
    "elements": dict,
    "popups": list,
    "shortcuts": list,
    "flows": list,
    "edges": dict,
    "mistakes": dict,
    "lessons": dict,
    "global": list,
    "disputed": list,
}

_LOCK = threading.Lock()
BY_RUN: OrderedDict = OrderedDict()


def empty() -> dict:
    return {key: make() for key, make in SNAPSHOT_KEYS.items()}


_ERRS = (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError)
_PLUG_SKIP = ("lessons", "global", "disputed")


def _merge_plugs(conn: object, facts: object, snap: dict) -> None:
    fill(conn, facts, snap)
    for key, value in knowledge_hooks.snapshots(conn, facts).items():
        if key in SNAPSHOT_KEYS and key not in _PLUG_SKIP:
            snap[key] = value


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
            _merge_plugs(conn, facts, snap)
        finally:
            conn.close()
    except _ERRS:
        logger.exception("knowledge snapshot load failed")
    return snap


def _run_env(package: str, run_id: object) -> object:
    path = app_knowledge._store_path(package) if package else None
    if path is None or not path.exists():
        return {}
    conn = app_knowledge._connect(path, write=False)
    try:
        row = conn.execute(
            "SELECT env_json FROM runs WHERE run_id = ?", (str(run_id),)
        ).fetchone()
        return json.loads(row[0]) if row else {}
    finally:
        conn.close()


def load_for_run(run_id: object) -> dict:
    """Load and register the snapshot of *run_id* (package and env from its manifest
    and run row). Always registers something, so a miss is not retried every turn."""
    snap = empty()
    try:
        content = run_store.read_manifest(str(run_id or "")).get("content") or {}
        package = str(content.get("package") or "")
        env = _run_env(package, run_id)
        facts = knowledge_run.RunFacts(
            run_id=str(run_id),
            package=package,
            env=env if isinstance(env, dict) else {},
        )
        snap = load(package, facts)
    except (*_ERRS, ImportError):
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
            BY_RUN[key] = snap
            BY_RUN.move_to_end(key)
            while len(BY_RUN) > limits.MAX_CACHED_RUNS:
                BY_RUN.popitem(last=False)
    except Exception:
        logger.exception("knowledge snapshot put failed")


def for_run(run_id: object) -> dict | None:
    try:
        with _LOCK:
            return BY_RUN.get(str(run_id or ""))
    except Exception:
        logger.exception("knowledge snapshot lookup failed")
        return None


def drop(run_id: object) -> None:
    try:
        with _LOCK:
            BY_RUN.pop(str(run_id or ""), None)
    except Exception:
        logger.exception("knowledge snapshot drop failed")

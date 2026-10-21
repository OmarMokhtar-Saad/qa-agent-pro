"""Where the run facts are written: the run manifest and the ``runs`` row."""

from __future__ import annotations

import json
import logging
import time

from tools.mobile import knowledge_db, run_store
from tools.mobile.knowledge_run_args import RunFacts
from tools.mobile.knowledge_run_env import parse_env

logger = logging.getLogger("tools.mobile.knowledge_run")

MAX_VERSIONS_SEEN = 50
_RUN_SQL = (
    "INSERT OR IGNORE INTO runs (run_id, app_version, version_code, env_json,"
    " started, learn_state) VALUES (?, ?, ?, ?, ?, 'unlearned')"
)


def _fresh(env: object) -> dict:
    clean = env if isinstance(env, dict) else parse_env(env)
    return {
        "env": dict(clean),
        "app_version": "",
        "version_code": "",
        "learn_state": "unlearned",
    }


def init_manifest(run_id: str, env: object = None) -> bool:
    """Write ``manifest["knowledge"]`` once at run creation. A later call never
    replaces an existing record (the first env wins). Never raises."""
    try:
        read = run_store.read_manifest(run_id) or {}
        body = read.get("content")
        if read.get("error") or not isinstance(body, dict):
            return False
        if isinstance(body.get("knowledge"), dict):
            return False
        body["knowledge"] = _fresh(env)
        return not run_store.write_manifest(run_id, body).get("error")
    except Exception:
        logger.exception("knowledge run: manifest init failed")
        return False


def store_state(run_id: str, state: str, reason: str = "") -> None:
    """Mirror ``runs.learn_state`` (and why) on the manifest. Never raises."""
    try:
        read = run_store.read_manifest(run_id) or {}
        body = read.get("content")
        if read.get("error") or not isinstance(body, dict):
            return
        know = body.setdefault("knowledge", {})
        if isinstance(know, dict) and know.get("learn_state") != state:
            know["learn_state"] = state
            know["learn_reason"] = reason
            run_store.write_manifest(run_id, body)
    except Exception:
        logger.exception("knowledge run: store_state failed")


def _store_version(run_id: str, name: str, code: str) -> None:
    """Record the version on the manifest once (first read wins)."""
    read = run_store.read_manifest(run_id) or {}
    body = read.get("content")
    if read.get("error") or not isinstance(body, dict):
        return
    know = body.setdefault("knowledge", {})
    if not isinstance(know, dict) or know.get("app_version"):
        return
    know["app_version"], know["version_code"] = name, code
    run_store.write_manifest(run_id, body)


def _insert_run(conn, facts: RunFacts) -> None:
    conn.execute(
        _RUN_SQL,
        (
            facts.run_id,
            facts.app_version[:60],
            facts.version_code[:20],
            json.dumps(facts.env, sort_keys=True),
            time.time(),
        ),
    )


def _note_version(conn, version: str) -> None:
    """meta.versions_seen: bounded JSON list, newest last, no duplicates."""
    if not version:
        return
    row = conn.execute("SELECT value FROM meta WHERE key='versions_seen'").fetchone()
    try:
        seen = list(json.loads(row[0])) if row else []
    except ValueError:
        seen = []
    if version in seen:
        return
    seen = (seen + [version])[-MAX_VERSIONS_SEEN:]
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('versions_seen', ?)",
        (json.dumps(seen),),
    )


def _write_run_row(facts: RunFacts) -> None:
    conn = knowledge_db.open_rw(facts.package)
    if conn is None:
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        _insert_run(conn, facts)
        knowledge_db.compact(conn, "runs")
        _note_version(conn, facts.app_version)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _begin_sync(facts: RunFacts) -> None:
    try:
        _write_run_row(facts)
        if facts.app_version:
            _store_version(facts.run_id, facts.app_version, facts.version_code)
    except Exception:
        logger.exception("knowledge run: begin failed")

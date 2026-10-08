"""Run-level facts for per-app knowledge: env, app version, the learn marker.

``env`` is a new optional call parameter ``"name=value,name=value"`` (at most
``ENV_ENTRIES`` entries). It is stored ONCE in the run manifest
(``manifest["knowledge"]``); a later differing env is ignored. An undeclared env
is ``{}`` and an env-scoped note then does NOT apply (fail closed).

``begin_run`` writes a ``runs`` row in state ``unlearned`` at run start and
fires a background resume of earlier unlearned runs of the same package. Nothing
here raises and nothing blocks the executor's hot path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

from tools.mobile import (
    app_info,
    app_knowledge,
    knowledge_db,
    knowledge_limits,
    run_store,
)

logger = logging.getLogger(__name__)

# One implementation: notes and run facts must agree on what an env scope means.
env_matches = app_knowledge.env_matches

_NAME_OK = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
MAX_ENV_VALUE = 200
MAX_VERSIONS_SEEN = 50
_FACTS: dict = {}
_MAX_FACTS = 64
_TASKS: set = set()
STATES = ("unlearned", "learning", "learned", "skipped", "failed")


@dataclass
class RunFacts:
    run_id: str = ""
    package: str = ""
    app_version: str = ""
    version_code: str = ""
    env: dict = field(default_factory=dict)
    lane: str = ""


def parse_env_checked(text: object) -> tuple[dict, str]:
    """``(env, error)``. An error means the text was refused and env is ``{}``."""
    raw = str(text or "").strip()
    if not raw:
        return {}, ""
    out: dict = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not _NAME_OK.match(name):
            return {}, "env entries look like name=value (names use A-Za-z0-9_.-)"
        if len(value) > MAX_ENV_VALUE:
            return {}, "env value for %s is longer than %d" % (name, MAX_ENV_VALUE)
        if name in out and out[name] != value:
            return {}, "env names %s twice" % name
        out[name] = value
    if len(out) > knowledge_limits.ENV_ENTRIES:
        return {}, "env has more than %d entries" % knowledge_limits.ENV_ENTRIES
    if out and app_knowledge.secret_reason(raw):
        return {}, "env looks like it holds a secret; it was not stored"
    return out, ""


def parse_env(text: object) -> dict:
    """The env named by *text*; ``{}`` when empty or refused."""
    return parse_env_checked(text)[0]


async def read_app_version(serial: str, package: str, *, shell=None) -> tuple[str, str]:
    """``(versionName, versionCode)`` of the installed app, ``("", "")`` on failure."""
    try:
        got = await app_info.read_version(serial, package, shell=shell)
        if got is None:
            return "", ""
        code = "" if got.version_code is None else str(got.version_code)
        return str(got.version_name or ""), code
    except Exception:
        logger.exception("knowledge: step failed")
        return "", ""


def _manifest_knowledge(run_id: str) -> dict:
    try:
        body = (run_store.read_manifest(run_id) or {}).get("content") or {}
        know = body.get("knowledge")
        return know if isinstance(know, dict) else {}
    except Exception:
        logger.exception("knowledge: step failed")
        return {}


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
        clean = env if isinstance(env, dict) else parse_env(env)
        body["knowledge"] = {
            "env": dict(clean),
            "app_version": "",
            "version_code": "",
            "learn_state": "unlearned",
        }
        return not run_store.write_manifest(run_id, body).get("error")
    except Exception:
        logger.exception("knowledge run: manifest init failed")
        return False


def _remember(facts: RunFacts) -> None:
    if len(_FACTS) >= _MAX_FACTS:
        _FACTS.pop(next(iter(_FACTS)))
    _FACTS[facts.run_id] = facts


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


def _write_run_row(facts: RunFacts) -> None:
    conn = knowledge_db.open_rw(facts.package)
    if conn is None:
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, app_version, version_code, env_json,"
            " started, learn_state) VALUES (?, ?, ?, ?, ?, 'unlearned')",
            (
                facts.run_id,
                facts.app_version[:60],
                facts.version_code[:20],
                json.dumps(facts.env, sort_keys=True),
                time.time(),
            ),
        )
        knowledge_db.compact(conn, "runs")
        _note_version(conn, facts.app_version)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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


def _begin_sync(facts: RunFacts) -> None:
    try:
        _write_run_row(facts)
        if facts.app_version:
            _store_version(facts.run_id, facts.app_version, facts.version_code)
    except Exception:
        logger.exception("knowledge run: begin failed")


async def begin_run(ctx: object) -> RunFacts:
    """Facts for this run, read ONCE (cached by run_id). Writes the ``runs`` row
    and fires the background resume. Never raises, never blocks the start."""
    run_id = str(getattr(ctx, "run_id", "") or "")
    package = str(getattr(ctx, "package", "") or "")
    try:
        if run_id and run_id in _FACTS:
            return _FACTS[run_id]
        known = await asyncio.to_thread(_manifest_knowledge, run_id) if run_id else {}
        env = getattr(ctx, "env", None) or known.get("env") or {}
        name, code = known.get("app_version") or "", known.get("version_code") or ""
        if not name and package:
            name, code = await read_app_version(
                str(getattr(ctx, "serial", "") or ""), package
            )
        facts = RunFacts(
            run_id,
            package,
            str(name),
            str(code),
            dict(env),
            str(getattr(ctx, "lane", "") or ""),
        )
        if run_id and package:
            _remember(facts)
            await asyncio.to_thread(_begin_sync, facts)
            _fire_resume(package, run_id)
        return facts
    except Exception:
        logger.exception("knowledge run: begin_run failed")
        return RunFacts(run_id, package)


def _fire_resume(package: str, current: str) -> None:
    try:
        from tools.mobile import knowledge_runner

        task = asyncio.get_running_loop().create_task(
            knowledge_runner.resume_pending(package, exclude=(current,))
        )
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    except Exception:
        logger.exception("knowledge run: resume not scheduled")


def mark_state(package: str, run_id: str, state: str, error: str = "") -> bool:
    """Set ``runs.learn_state``. ``learning`` counts a try. Never raises."""
    if state not in STATES:
        return False
    conn = knowledge_db.open_rw(package)
    if conn is None:
        return False
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, started) VALUES (?, ?)",
            (run_id, time.time()),
        )
        tries = ", learn_tries = learn_tries + 1" if state == "learning" else ""
        learned = ", learned_at = %f" % time.time() if state == "learned" else ""
        conn.execute(
            "UPDATE runs SET learn_state = ?, learn_error = ?%s%s WHERE run_id = ?"
            % (tries, learned),
            (state, str(error or "")[:300], run_id),
        )
        conn.commit()
        return True
    except Exception:
        logger.exception("knowledge run: mark_state failed")
        try:
            conn.rollback()
        except Exception:
            logger.exception("knowledge: step failed")
        return False
    finally:
        conn.close()


def _finished(run_id: str, started: float, now: float) -> bool:
    """A run is finished when its manifest records ``final``, or it began more
    than STALE_RUN_S ago (abandoned runs never get a ``final``)."""
    try:
        body = (run_store.read_manifest(run_id) or {}).get("content")
        if isinstance(body, dict) and isinstance(body.get("final"), dict):
            return True
    except Exception:
        logger.exception("knowledge: step failed")
    return bool(started) and now - float(started) > knowledge_limits.STALE_RUN_S


def pending_runs(package: str, *, exclude=(), limit: int | None = None) -> list[str]:
    """Run ids to learn: unlearned, or failed with fewer than 3 tries, and
    finished. Oldest first. Never raises."""
    cap = knowledge_limits.PENDING_RESUME_LIMIT if limit is None else int(limit)
    conn = knowledge_db.open_rw(package)
    if conn is None:
        return []
    try:
        skip = {str(x) for x in (exclude or ())}
        rows = conn.execute(
            "SELECT run_id, started FROM runs WHERE learn_state = 'unlearned'"
            " OR (learn_state = 'failed' AND learn_tries < 3) ORDER BY started"
        ).fetchall()
        now = time.time()
        out = [
            r[0] for r in rows if r[0] not in skip and _finished(r[0], r[1] or 0.0, now)
        ]
        return out[: max(0, cap)]
    except Exception:
        logger.exception("knowledge run: pending_runs failed")
        return []
    finally:
        conn.close()

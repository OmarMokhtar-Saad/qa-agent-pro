"""Replay knowledge: loading an app's saved guards and its read-only snapshot."""

from __future__ import annotations

import logging
from typing import Any, cast

from tools.mobile import app_knowledge, knowledge_hooks, knowledge_run
from tools.mobile.replay.context import Context

logger = logging.getLogger("tools.mobile.executor")


def _known(ctx: Context) -> dict:
    """The loaded notes: callers run only after ``_load_knowledge`` found some."""
    return cast(dict, ctx.knowledge)


def _load_knowledge(ctx: Context) -> None:
    """Load this app's saved-note guards ONCE per replay. Read-only: an app
    with no store creates nothing. Never raises; any failure is "no notes"."""
    if getattr(ctx, "knowledge", None) is not None:
        return
    ctx.knowledge = _read_knowledge(str(getattr(ctx, "package", "") or ""))


def _load_guards(package: str) -> list:
    """*package*'s saved guards; ``[]`` for none or on any failure."""
    try:
        if package:
            loaded = app_knowledge.load_guards(package)
            if not loaded.get("error"):
                return list(loaded.get("content") or [])
    except Exception:
        logger.exception("mobile app knowledge: load failed; continuing without notes")
    return []


def _read_knowledge(package: str, facts: Any = None) -> dict:
    """*package*'s guards plus empty tallies. Touches no Context, so a worker
    thread still running after its awaiting task was cancelled cannot set
    ``ctx.knowledge`` behind ``_flush_knowledge``'s back. Never raises.

    Env-scoped guards are filtered here against ``facts.env``: a match is
    stamped ``_env_ok`` (so ``candidates`` applies it), a mismatch goes to
    ``skipped_env``. ``snap`` holds the plugs' read-only snapshot."""
    guards, skipped = _split_env(_load_guards(package), getattr(facts, "env", None))
    return {
        "package": package,
        "guards": guards,
        "counts": {},
        "events": [],
        "spent_ms": 0,
        "skipped_env": skipped,
        "snap": _read_snapshot(package, facts),
    }


def _split_env(guards: list, env: Any) -> tuple[list, list]:
    """Keep guards with no env condition or a matching one (stamped ``_env_ok``);
    the rest are skipped. An empty run env fails closed."""
    kept: list = []
    skipped: list = []
    for guard in guards:
        want = (guard.get("when") or {}).get("env")
        if not want:
            kept.append(guard)
        elif knowledge_run.env_matches(want, env):
            kept.append(dict(guard, _env_ok=True))
        else:
            skipped.append(guard)
    return kept, skipped


def _read_snapshot(package: str, facts: Any) -> dict:
    """The plugs' merged read-only snapshot; ``{}`` without facts or an existing
    store (nothing is created). One connection, closed here. Never raises."""
    if facts is None or not package:
        return {}
    try:
        path = app_knowledge.db_path(package)
        if path is None or not path.exists():
            return {}
        conn = app_knowledge._connect(path, write=False)
        try:
            return knowledge_hooks.snapshots(conn, facts)
        finally:
            conn.close()
    except Exception:
        logger.exception("mobile app knowledge: snapshot failed")
        return {}

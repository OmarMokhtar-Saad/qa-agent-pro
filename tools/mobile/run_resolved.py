"""The cross-run resolved-app cache (task hint -> package, serial, charter).

:func:`read_resolved_app` returns the cached ``{package, serial, charter}`` for a
task key, or ``{}`` on a miss or an expired entry. Never raises -- a miss is the
normal first-run case, not an error.

:func:`write_resolved_app` remembers package/serial/charter for a task key.
Never raises. Bounded to MAX_RESOLVED_APPS entries (oldest `cached_at` evicted
first) and carries no secrets -- a package id, a device serial and the charter's
own (already-disclosed) destructive policy, nothing from the run's own steps or
the app's data.
"""

from __future__ import annotations

import logging
import time
from typing import NamedTuple

from tools.mobile import paths, run_consts, run_io

logger = logging.getLogger("tools.mobile.run_store")


class ResolvedApp(NamedTuple):
    package: str
    serial: str
    charter: dict | None = None


def _resolved_app_key(task_key: str) -> str:
    """Normalise a task/app hint to a stable cache key. No secrets stored."""
    return str(task_key or "").strip().lower()[:200]


def _fresh_entry(store: object, key: str) -> dict:
    if not isinstance(store, dict):
        return {}
    entry = store.get(key) or {}
    if not entry:
        return {}
    if (
        time.time() - float(entry.get("cached_at") or 0)
    ) > run_consts.RESOLVED_APP_TTL_S:
        return {}
    return {
        "package": str(entry.get("package") or ""),
        "serial": str(entry.get("serial") or ""),
        "charter": (
            entry.get("charter") if isinstance(entry.get("charter"), dict) else {}
        ),
    }


# Cached ``{package, serial, charter}`` for *task_key*, or ``{}`` on a
# miss or an expired entry. Never raises -- a miss is the normal first-run
# case, not an error.
def read_resolved_app(task_key: str) -> dict:
    """Cached ``{package, serial, charter}`` for *task_key*, or ``{}``."""
    try:
        key = _resolved_app_key(task_key)
        if not key:
            return {}
        store = run_io._read_json(paths.state_file(run_consts.RESOLVED_APPS_FILE)) or {}
        return _fresh_entry(store, key)
    except Exception:
        logger.exception("mobile.run_store.read_resolved_app failed")
        return {}


def _entry_for(package: str, serial: str, charter: dict) -> dict:
    return {
        "package": str(package or ""),
        "serial": str(serial or ""),
        "charter": charter if isinstance(charter, dict) else {},
        "cached_at": time.time(),
    }


def _evict_oldest(store: dict) -> None:
    if len(store) > run_consts.MAX_RESOLVED_APPS:
        oldest = sorted(store.items(), key=lambda kv: kv[1].get("cached_at") or 0)
        for stale_key, _ in oldest[: len(store) - run_consts.MAX_RESOLVED_APPS]:
            store.pop(stale_key, None)


def write_resolved_app(task_key: str, app: ResolvedApp) -> None:
    """Remember *app* (package/serial/charter) for *task_key*. Never raises."""
    try:
        key = _resolved_app_key(task_key)
        if not key:
            return
        path = paths.state_file(run_consts.RESOLVED_APPS_FILE)
        store = run_io._read_json(path) or {}
        if not isinstance(store, dict):
            store = {}
        store[key] = _entry_for(app.package, app.serial, app.charter or {})
        _evict_oldest(store)
        run_io._write_json(path, store)
    except Exception:
        logger.exception("mobile.run_store.write_resolved_app failed")

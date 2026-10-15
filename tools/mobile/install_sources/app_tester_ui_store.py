"""App Tester UI: the small on-disk store behind the lookup memory.

One JSON file in the mobile ``state/`` directory, ``{section: {key: {v, at}}}``.
It survives the process, so a cold call reuses what an earlier one learned. A
wrong entry costs one verifying read, never a wrong tap, and every entry
expires. Never raises: a store that cannot be read or written is a miss.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from tools.mobile import paths, run_io

logger = logging.getLogger("tools.mobile.install_sources.app_tester_ui_store")

FILE = "app_tester_memory.json"
#: Seconds an entry stays trusted, per section (the latest build changes often).
WEEK_S = 7 * 24 * 3600
TTL_S = {
    "pages": WEEK_S,
    "spages": WEEK_S,
    "rows": WEEK_S,
    "ids": WEEK_S,
    "latest": 15 * 60,
}
MAX_PER_SECTION = 200


def _load() -> dict:
    data = run_io.read_json(paths.state_file(FILE))
    return data if isinstance(data, dict) else {}


def _evict(section: dict) -> None:
    if len(section) > MAX_PER_SECTION:
        oldest = sorted(section.items(), key=lambda kv: kv[1].get("at") or 0)
        for key, _ in oldest[: len(section) - MAX_PER_SECTION]:
            section.pop(key, None)


def get(section: str, key: str) -> Any:
    """The fresh value stored under *key*, else ``None``."""
    try:
        entry = (_load().get(section) or {}).get(key)
        if not isinstance(entry, dict):
            return None
        if time.time() - float(entry.get("at") or 0) > TTL_S[section]:
            return None
        return entry.get("v")
    except Exception:
        logger.exception("app_tester_ui_store.get failed")
        return None


def put(section: str, key: str, value: Any) -> None:
    try:
        data = _load()
        bucket = data.get(section)
        bucket = bucket if isinstance(bucket, dict) else {}
        bucket[key] = {"v": value, "at": time.time()}
        _evict(bucket)
        data[section] = bucket
        run_io.write_json(paths.state_file(FILE), data)
    except Exception:
        logger.exception("app_tester_ui_store.put failed")


def drop(section: str, key: str) -> None:
    try:
        data = _load()
        if (data.get(section) or {}).pop(key, None) is not None:
            run_io.write_json(paths.state_file(FILE), data)
    except Exception:
        logger.exception("app_tester_ui_store.drop failed")


def clear() -> None:
    try:
        paths.state_file(FILE).unlink(missing_ok=True)
    except Exception:
        logger.exception("app_tester_ui_store.clear failed")

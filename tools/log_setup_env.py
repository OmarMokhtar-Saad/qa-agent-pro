"""The log settings: ``os.environ`` first, then a lenient scan of the install's .env.

Part of the ``tools.log_setup`` facade; see its docstring for the design.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tools.process_env import get_env

logger = logging.getLogger("qa_agents.log_setup")

# tools/log_setup_env.py -> tools/ -> the install root.
INSTALL_ROOT = Path(__file__).resolve().parent.parent
env_cache: dict[str, str] = {}


def _read_env_text() -> str | None:
    """The install's .env text (capped at 1 MiB), or None when there is none."""
    env_path = INSTALL_ROOT / ".env"
    if not env_path.is_file():
        return None
    with env_path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(1024 * 1024)


def _log_knobs(raw: str) -> dict[str, str]:
    """The ``QA_LOG_*`` assignments of a .env text, upper-cased keys."""
    found: dict[str, str] = {}
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        name = key.strip().upper()
        # 2026-08-09 (review L1): cache ONLY the knobs this module reads. The
        # scan used to keep every .env value -- including five API secrets --
        # resident for the whole process lifetime in order to answer three
        # QA_LOG_* lookups.
        if not name.startswith("QA_LOG_"):
            continue
        found[name] = value.split("#")[0].strip().strip("\"'")
    return found


# The install's .env as a flat dict. Cached, capped, never raises.
#
# ``config/settings`` parses .env into pydantic, NOT into ``os.environ``, and this
# module must stay stdlib-only (bar the leaf ``tools.process_env``) and
# import-cheap (the dist launcher imports it
# before ``tools/updater`` may swap the tree, and importing settings from there
# would be a new startup dependency). So the three retention knobs are read here:
# ``os.environ`` first, then a lenient scan of the install's own .env.
def _env_file_values() -> dict[str, str]:
    """The install's .env as a flat dict. Cached, capped, never raises."""
    if env_cache:
        return env_cache
    env_cache["__loaded__"] = "1"
    try:
        raw = _read_env_text()
        if raw is not None:
            env_cache.update(_log_knobs(raw))
    except OSError:
        logger.debug("could not read .env for the log settings", exc_info=True)
    return env_cache


def _setting(name: str) -> str:
    value = get_env(name)
    if value is not None and value.strip():
        return value.strip()
    return _env_file_values().get(name, "")


def _bool_setting(name: str, default: bool) -> bool:
    raw = _setting(name).lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _int_setting(name: str, default: int, minimum: int) -> int:
    try:
        raw = _setting(name)
        return max(minimum, int(float(raw))) if raw else default
    except (ValueError, OverflowError):
        logger.debug("unusable %s -- using %s", name, default, exc_info=True)
        return default


def _float_setting(name: str, default: float, minimum: float) -> float:
    try:
        raw = _setting(name)
        return max(minimum, float(raw)) if raw else default
    except (ValueError, OverflowError):
        logger.debug("unusable %s -- using %s", name, default, exc_info=True)
        return default


# Retention. A file goes when it is older than RETENTION_DAYS *or* beyond the newest
# RETENTION_MAX_FILES. Deleting logs is destructive -- the file swept could be the
# one a diagnoser wanted -- so an operator can widen or disable it without touching
# code, while the DEFAULT stays on: this is the bug fix, and an unbounded directory
# on a multi-client install is its own incident. Every knob is lenient and
# never-raising, exactly like config/settings' coercers.
RETENTION_ENABLED = _bool_setting("QA_LOG_RETENTION_ENABLED", True)
RETENTION_DAYS = _float_setting("QA_LOG_RETENTION_DAYS", 14.0, 1.0)
RETENTION_MAX_FILES = _int_setting("QA_LOG_RETENTION_MAX_FILES", 40, 2)

_env_cache = env_cache  # legacy name (WX removes it)

_INSTALL_ROOT = INSTALL_ROOT  # legacy name (WX removes it)

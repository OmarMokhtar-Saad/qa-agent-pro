"""Moved from handlers (refactor P2): _common."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import tools.updater as updater
from tools import clock

# Pinned to the pre-split name: log filters, caplog and handlers key on it.
logger = logging.getLogger("tools.mcp_handlers")


def _boot_version() -> str:
    """The installed version as it was at IMPORT time. Compared against the
    on-disk VERSION later to notice that another process updated this install
    underneath a running server. Never raises."""
    try:
        return updater._local_version(Path(updater._INSTALL_DIR)) or ""
    except (OSError, ValueError):
        return ""


BOOT_VERSION = _boot_version()


# Wall-clock at import: anything on disk newer than this was written AFTER the
# running server read its configuration.
_PROCESS_START = clock.now()


def _env_semantic_lines(text: str) -> list[str]:
    """The SETTING lines of a .env: blank lines, full-line comments and
    surrounding whitespace removed."""
    out: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _env_semantic_fingerprint(text: str) -> str:
    """sha256 over a .env's semantic lines. Never raises ("" on failure)."""
    try:
        digest = hashlib.sha256()
        for line in _env_semantic_lines(text):
            digest.update(line.encode("utf-8"))
            digest.update(b"|")
        return digest.hexdigest()
    except ValueError:
        logger.debug("could not fingerprint .env content", exc_info=True)
        return ""


def _env_fingerprint() -> str:
    """Fingerprint of the install's .env ("" when absent or unreadable)."""
    try:
        env_path = updater._INSTALL_DIR / ".env"
        if not env_path.is_file():
            return ""
        with env_path.open("r", encoding="utf-8", errors="replace") as handle:
            # Capped: a .env is a few KiB, and this runs on a tool call.
            raw = handle.read(1024 * 1024)
        return _env_semantic_fingerprint(raw)
    except (ImportError, OSError, ValueError):
        logger.debug("could not read .env for the reload check", exc_info=True)
        return ""


# The .env this process actually booted with. Compared against, never re-read.
# NOTE: _PROCESS_START above is no longer read by THIS function -- do not delete
# it. tests/test_setup_check_env_reload.py and tests/test_setup_check_reload_marker.py
# still build their fixtures around it.
BOOT_ENV_FINGERPRINT = _env_fingerprint()

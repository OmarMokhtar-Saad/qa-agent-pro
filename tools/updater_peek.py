"""Read-only update lookup: is a newer release out? Installs nothing."""

from __future__ import annotations

from pathlib import Path

import httpx

from config.settings_guard import never_raise
from tools import settings_port
from tools.updater_github import fetch_latest_release
from tools.updater_version import _local_version as local_version
from tools.updater_version import is_newer

_INSTALL_DIR = Path(__file__).resolve().parent.parent


def peek_update(repo: str) -> str:
    """``"available"`` when a newer release exists, ``"up-to-date"``, or
    ``"error"``. Unlike ``run_update_check`` it never downloads or swaps, so a
    session that is mid-run can ask without being restarted under itself."""
    with never_raise():
        try:
            conf = settings_port.get()
            release = fetch_latest_release(
                repo, (conf.github_token or "").strip(), conf.qa_update_timeout
            )
            tag = (release or {}).get("tag_name")
            if not tag:
                return "error"
            newer = is_newer(tag, local_version(_INSTALL_DIR))
            return "available" if newer else "up-to-date"
        except httpx.HTTPError:
            return "error"
    return "error"

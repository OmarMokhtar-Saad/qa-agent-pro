"""The reload outcome note and the drift advisory (moved from setup_check)."""

from __future__ import annotations

import logging
import os

from config.settings_guard import never_raise
from handlers import _common, export

logger = logging.getLogger("tools.mcp_handlers")


def _not_taking_effect(now_v: str) -> tuple[str, str]:
    """The (note, action) pair for a reload that left the old process running."""
    shown = f"v{now_v}" if now_v else "the version it started with"
    return (
        f"⚠️ **Reload did not take effect** — still running {shown}. If this "
        "persists, restart the editor (quit and reopen it) or reinstall the "
        "agent.",
        f"The last reload did not take effect — still running {shown}. Quit "
        "and reopen the editor; if it still says this, reinstall the agent.",
    )


def _version_shift(prev_v: str, now_v: str) -> str:
    """The "vOLD → vNEW" text, or just the running version when it did not move."""
    return (
        f"v{prev_v} → v{now_v}"
        if prev_v and now_v and prev_v != now_v
        else f"v{now_v or 'unknown'}"
    )


# A reload took effect when a DIFFERENT process is answering now, so the pid
# is the primary signal. The version is secondary, and only for the reasons
# that are SUPPOSED to change it: an "update"/"code" reload that came back on
# the same version means the launcher respawned the same tree. A "config"
# reload is expected to keep its version, so judging it on the version would
# report every successful .env reload as a failure.
def reload_outcome(marker: dict) -> tuple[str, str]:
    """Turn a fresh marker into ``(report_note, action_item)``; both empty when there is nothing to say."""
    if not marker:
        return "", ""
    now_v = _common.BOOT_VERSION or ""
    prev_v = str(marker.get("version") or "")
    same_process = marker.get("pid") == os.getpid()
    stuck = (
        str(marker.get("reason") or "") in ("update", "code")
        and bool(prev_v)
        and prev_v == now_v
    )
    if same_process or stuck:
        return _not_taking_effect(now_v)
    return f"✅ **Reloaded successfully** ({_version_shift(prev_v, now_v)})", ""


_DRIFT_CODE_NOTE = (
    "**This server is running older code than is on disk.** Another "
    "process updated this install after this one started, so it is "
    "serving stale modules -- and anything reported below describes "
    "the OLD code. Restart the MCP server (or reconnect it in "
    "your client) to pick the new code up."
)

_DRIFT_ENV_NOTE = (
    "**`.env` was edited after this server started.** The settings "
    "reported above are the ones this process booted with, NOT what "
    "the file says now. Restart the MCP server (or reconnect it in "
    "your client) to apply them."
)


# 2026-08-31 (qa-doctor/qa-restart use-case sweep): the two drift checks
# below were reachable ONLY from inside `if test_cases_only() and
# _DIST_UPDATE_REPO:`. `_DIST_UPDATE_REPO` is "" in every private checkout
# and in any dist build without an update repo, so on those installs
# qa-doctor rendered settings from the `.env` it BOOTED with and said
# nothing -- the exact staleness those checks exist to catch, silent on the
# installs where a developer edits `.env` most often.
#
# It is an ADVISORY here, not a reload, and that difference is the whole
# reason this is a separate function: `schedule_reload` hard-exits the
# process, which is safe only because a supervising launcher respawns it and
# replays the handshake. An unsupervised server that exited would be marked
# `failed` by the client and respawned by nobody, so turning a stale-config
# notice into a dead session is strictly worse than reporting it. Recommended
# rather than blocking: the server still answers every tool call correctly,
# it is just answering on older configuration.
#
# Returns "" when nothing drifted, which is the normal case.
def drift_advisory() -> str:
    """qa-doctor's drift line for an install that cannot reload itself."""
    with never_raise(logger, "drift advisory failed", exc_info=True) as g:
        code = export.code_changed_since_start()
        env = export.env_changed_since_start()
    if g.failed:
        return ""
    if code:
        return _DRIFT_CODE_NOTE
    if env:
        return _DRIFT_ENV_NOTE
    return ""

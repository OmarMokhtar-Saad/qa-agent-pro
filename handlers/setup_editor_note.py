"""The one-time editor-restart note of qa-doctor (moved from setup_check)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tools import updater

logger = logging.getLogger("tools.mcp_handlers")

# FROZEN-SCHEMA POLICY: editors cache tool definitions for the whole session
# and ignore list_changed, so a signature change is invisible until the editor
# restarts. Keep tool names/params stable; when a release DOES change one,
# bump this to that release version — qa-doctor then tells users whose
# session predates it that a one-time editor restart is needed.
_TOOL_SCHEMAS_CHANGED_IN = "1.14.0"


# 2026-08-31: `except (OSError, ValueError)` covers an unreadable or
# unparseable file, but NOT a file holding valid JSON that is not an
# object -- `null` or `[]` reached `.get` and raised AttributeError.
# The outer handler then replaced the ENTIRE report with "Setup
# check failed: 'list' object has no attribute 'get'", so a tester
# on a machine with one torn sidecar lost every other diagnosis in
# the tool that exists to give them one. This file is written by a
# different process and can be truncated mid-write, which is exactly
# how a half-flushed object becomes something else.
def _session_client_version(install_dir: Path) -> str:
    try:
        state = json.loads(
            (install_dir / "backups" / "session-state.json").read_text(encoding="utf-8")
        )
        if not isinstance(state, dict):
            raise ValueError("session-state.json is not a JSON object")
        return str(state.get("client_schema_version") or "")
    except (OSError, ValueError, AttributeError, TypeError):
        return ""


def _editor_restart_note(install_dir: Path) -> str:
    client_v = _session_client_version(install_dir)
    if not client_v:
        return ""

    have = updater._parse_version(client_v)
    need = updater._parse_version(_TOOL_SCHEMAS_CHANGED_IN)
    if have is not None and need is not None and have < need:
        return (
            "⚠️ **One-time editor restart needed** — this editor session "
            f"loaded the agent's tool definitions at v{client_v}, but "
            f"they changed in v{_TOOL_SCHEMAS_CHANGED_IN}. Editors do "
            "not refresh tool definitions mid-session: quit and reopen "
            "the editor (Cmd+Q on macOS) to load the latest "
            "capabilities. Everything else updates automatically."
        )
    return ""

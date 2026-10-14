"""Jira MCP: local config helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings_guard import never_raise
from tools.process_env import home_dir

logger = logging.getLogger("tools.jira_mcp")


def _workspace_config_paths(
    workspace_roots: list[Path] | None, parts: tuple[str, ...]
) -> list[Path]:
    """Config paths under each open workspace root; a garbage root is dropped."""
    paths: list[Path] = []
    for root in workspace_roots or []:
        try:
            paths.append(Path(root).joinpath(*parts))
        except (TypeError, ValueError):
            logger.debug("ignoring unusable workspace root %r", root, exc_info=True)
    return paths


# Where THIS server's own files live. A module constant rather than an inline
# expression so tests can point it somewhere harmless -- the same seam
# tools/updater._INSTALL_DIR already provides.
INSTALL_ROOT = Path(__file__).resolve().parent.parent


# A local mcp.json is a hand-written config file; anything larger than this is
# not one, and must not be read into memory just to be rejected.
MAX_LOCAL_CONFIG_BYTES = 1_000_000


def local_home_dir() -> Path | None:
    """The user's home directory, or None when it cannot be determined.
    Never raises (Path.home() can raise on an environment with no HOME)."""
    with never_raise():
        return home_dir()
    return None


# Known on-disk MCP config locations for a client, most authoritative first.
#
# Project-scoped config lives in the tester's OPEN EDITOR WORKSPACE, which a
# stdio subprocess cannot infer on its own. Two guesses shipped on that
# reasoning and BOTH were confirmed wrong against live qa-agent-pro sessions:
#
# * this server's own install directory (v1.31.0) -- a dist install lives in a
#   fixed place (e.g. ~/.qa-agents/) while the tester can have ANY project
#   open in their editor;
# * ``Path.cwd()`` (v1.32.0) -- an editor does NOT reliably spawn the MCP
#   subprocess with the workspace as its working directory, and inside THIS
#   server it is provably useless: mcp_server chdir()s to its own install root
#   at import time, so cwd collapses onto the candidate above.
#
# The MCP protocol carries the real signal: the client's ``roots`` capability
# reports the open workspace folder(s). ``mcp_server.qa-doctor`` resolves
# it (``ctx.list_roots()``) and threads the result down as ``workspace_roots``,
# which is checked FIRST. The two guesses stay on as low-priority candidates --
# a dev checkout runs the server FROM the workspace, so they are right there --
# and a client with no ``roots`` support passes ``None``, degrading to exactly
# the previously shipped chain. Never raises: an unreadable cwd, an
# unknowable home, or a garbage roots entry just drops that candidate.
#
# Only clients with a well-defined local JSON config are covered here --
# Claude Desktop's Atlassian connection is a hosted claude.ai Connector
# with no local file, so it is intentionally absent.
def _local_config_paths(
    client_key: str, workspace_roots: list[Path] | None = None
) -> list[Path]:
    """Known on-disk MCP config locations for a client, most authoritative first."""
    if client_key not in ("cursor", "claude-code"):
        return []

    parts = (".cursor", "mcp.json") if client_key == "cursor" else (".mcp.json",)
    candidates = _workspace_config_paths(workspace_roots, parts)
    candidates.append(INSTALL_ROOT.joinpath(*parts))
    try:
        candidates.append(Path.cwd().joinpath(*parts))
    except OSError:
        pass

    if client_key == "cursor":
        home = local_home_dir()
        if home is not None:
            candidates.append(home / ".cursor" / "mcp.json")
    return list(dict.fromkeys(candidates))


# Best-effort, read-only check for an 'atlassian' MCP server entry
# already present in a known on-disk config file for this client.
#
# ``workspace_roots`` (the tester's OPEN workspace, from the MCP ``roots``
# capability) is searched before the local fallbacks -- see
# ``_local_config_paths`` for why nothing else on disk can be trusted to be
# the tester's project.
#
# This can only prove the entry is CONFIGURED, never that it is actually
# CONNECTED/AUTHORIZED -- that live OAuth state lives inside the editor's
# own MCP client runtime, which a sibling stdio process cannot observe.
# Callers must phrase around that gap. Never raises: an oversized, absent,
# unreadable or non-object config is skipped, not fatal.
def local_atlassian_entry_exists(
    client_key: str, workspace_roots: list[Path] | None = None
) -> bool:
    """Best-effort, read-only check for an 'atlassian' MCP entry in local config."""
    for path in _local_config_paths(client_key, workspace_roots):
        try:
            if path.stat().st_size > MAX_LOCAL_CONFIG_BYTES:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if "atlassian" in (data.get("mcpServers") or {}):
                return True
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return False


def atlassian_entry_configured(workspace_roots: list[Path] | None = None) -> bool:
    """Read-only: is an ``atlassian`` entry on disk for ANY file-configured client?

    Covers Cursor and Claude Code (the clients whose entry is a local JSON file),
    searching ``workspace_roots`` first exactly as ``local_atlassian_entry_exists``
    does. True proves the entry is CONFIGURED, never that it is connected or
    authorized. qa-doctor uses it to decide whether Jira is already in use.
    Never raises.
    """
    for key in ("cursor", "claude-code"):
        with never_raise() as g:
            if local_atlassian_entry_exists(key, workspace_roots=workspace_roots):
                return True
        if g.failed:
            continue
    return False

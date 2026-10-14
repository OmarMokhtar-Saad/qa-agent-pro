"""Advisory warnings of tools.client_registry (split out of client_registry.py)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tools.clireg_consts import _RECOVERABLE
from tools.clireg_scan import discover_registrations, install_target

logger = logging.getLogger("qa_agents.client_registry")

# This module's own directory is tools/, so its grandparent is the server's
# install root -- computed locally rather than imported from
# handlers, per this module's stdlib-only/imports-no-project-module
# house rule (bar the leaf ``tools.process_env``) above.
_SERVER_ROOT = Path(__file__).resolve().parent.parent

_SPLIT_TAIL = (
    ". Run one whole flow against ONE of them. An agent that prepares "
    "against one and submits to the other is mixing two installs, which "
    "have separate `.env` files and therefore different feature flags -- "
    "the suite then gets prepared under one configuration and finalized "
    "under another. A project-scope entry only exists while that folder is "
    "open, so this is easy to hit without having changed anything."
)

_WS_WARNING = (
    "**Your open workspace contains this MCP server's own source "
    "tree** (`{root}`). An agent running in this workspace can "
    "import `handlers/` directly or start its own copy "
    "of this server, bypassing the registered tool surface and its "
    "dispatch guard entirely. Point your editor at a dedicated "
    "workspace instead, e.g. `~/qa-onboarding-ws`, and keep this "
    "install's checkout closed while you run QA sessions."
)


def _install_groups(found: list[dict]) -> dict:
    by_install: dict = {}
    for r in found:
        key = install_target(r["command"], r.get("base") or r["config"]) or r["name"]
        by_install.setdefault(key, []).append(r)
    return by_install


def _split_text(by_install: dict) -> str:
    lines = []
    for target, rows in sorted(by_install.items()):
        names = ", ".join(sorted({r["name"] for r in rows}))
        scopes = ",".join(sorted({r["scope"] for r in rows}))
        lines.append(f"`{names}` ({scopes}) at `{target}`")
    head = f"**{len(by_install)} DIFFERENT qa-agents installs are registered** "
    return head + "in your MCP configs: " + "; ".join(lines) + _SPLIT_TAIL


# Grouped by resolved install directory, not by name or raw command: three
# clients all pointing at the same packaged install is normal and silent. Two
# different installs is the hazard -- an agent can prepare against one and
# submit against the other, and they have separate `.env` files, so the suite is
# prepared under one set of feature flags and finalized under another. Returns
# "" when there is nothing to say. Never raises.
def split_server_warning(
    home: Path | None = None,
    workspace_roots: list | None = None,
) -> str:
    """One advisory line when MORE THAN ONE distinct qa INSTALL is registered."""
    try:
        by_install = _install_groups(discover_registrations(home, workspace_roots))
        if len(by_install) < 2:
            return ""
        return _split_text(by_install)
    except _RECOVERABLE:
        logger.debug("split_server_warning failed", exc_info=True)
        return ""


def root_is_relative(inner: Path, outer: Path) -> bool:
    """True when ``inner`` is ``outer`` or nested under it. Never raises."""
    try:
        inner.relative_to(outer)
        return True
    except ValueError:
        return False


def _resolve_root(server_root: Path | None) -> Path:
    root = Path(server_root) if server_root is not None else _SERVER_ROOT
    try:
        return root.resolve()
    except (OSError, RuntimeError):
        return root


def _resolve_candidate(raw: Any) -> Path | None:
    try:
        return Path(raw).resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _overlaps(root: Path, candidate: Path) -> bool:
    if candidate == root:
        return True
    return root_is_relative(root, candidate) or root_is_relative(candidate, root)


# A workspace that contains ``handlers/`` lets an agent import
# that module directly, or spawn its own stdio copy of this server inside
# its own environment, bypassing the registered tool surface and its
# dispatch guard entirely -- exactly how the 2026-09-25 Cursor audit
# session reached qa_prepare's internals without ever calling the real
# tool. Returns "" when there is nothing to say. Never raises.
def workspace_contains_server_warning(
    workspace_roots: list | None = None,
    server_root: Path | None = None,
) -> str:
    """One advisory line when the tester's OPEN WORKSPACE contains this server's source."""
    try:
        root = _resolve_root(server_root)
        if not (root / "handlers" / "__init__.py").is_file():
            return ""
        for raw in workspace_roots or []:
            candidate = _resolve_candidate(raw)
            if candidate is not None and _overlaps(root, candidate):
                return _WS_WARNING.format(root=root)
        return ""
    except _RECOVERABLE:
        logger.debug("workspace_contains_server_warning failed", exc_info=True)
        return ""

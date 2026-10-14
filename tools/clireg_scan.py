"""Discovery of qa-server registrations (split out of client_registry.py)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

from tools.clireg_consts import _RECOVERABLE
from tools.clireg_spec import _Src
from tools.clireg_targets import _PROJECT_CONFIG_RELPATHS, default_targets
from tools.process_env import home_dir

logger = logging.getLogger("qa_agents.client_registry")


def _looks_like_qa_server(name: str) -> bool:
    """Whether an MCP server entry name is one of ours.

    Deliberately name-based and generous (`qa-agents`, `qa-agent-pro`,
    `qa_agent_dev`, ...): the point is to notice a SECOND one, so a false positive
    costs an informational line while a false negative hides the split entirely.
    """
    low = (name or "").lower()
    return "qa" in low and ("agent" in low or "agents" in low)


def _sources(home: Path, workspace_roots: list | None) -> Iterator[_Src]:
    yield _Src(home / ".claude.json", "user")
    for _label, cfg, _need in default_targets(home):
        yield _Src(cfg, "user")
    for root in list(workspace_roots or []):
        for rel in _PROJECT_CONFIG_RELPATHS:
            yield _Src(Path(root) / rel, "project", Path(root))


# 2026-08-03. A real run staged its prep on one install and finalized it on
# another, because the packaged install is registered in the USER configs while
# this project's own `.mcp.json` / `.cursor/mcp.json` register a DEV checkout --
# so anyone who opens the repo has two qa servers live at once and an agent can
# pick a different one per call. The version-skew warning catches it after the
# fact; this is how `qa-doctor` can warn BEFORE a suite is built.
#
# Returns dicts of {scope, name, command, config}. Never raises: a missing or
# malformed config is skipped, because this only produces an advisory line.
def discover_registrations(
    home: Path | None = None,
    workspace_roots: list | None = None,
) -> list[dict]:
    """Every qa-server MCP registration visible on this machine. READ-ONLY."""
    out: list[dict] = []
    home = Path(home) if home is not None else home_dir()
    seen_paths: set = set()
    for src in _sources(home, workspace_roots):
        _scan_registrations(src, seen_paths, out)
    return out


def _entry_command(entry: object) -> str:
    if not isinstance(entry, dict):
        return ""
    cmd = str(entry.get("command") or entry.get("url") or "")
    args = entry.get("args")
    if isinstance(args, list) and args:
        cmd = (cmd + " " + " ".join(str(a) for a in args)).strip()
    return cmd


# A relative path in a PROJECT config is workspace-relative,
# not config-relative -- `${workspaceFolder}` means the
# workspace ROOT. Resolving against the config's own folder
# made `.cursor/mcp.json` look like a third install living in
# `<root>/.cursor`. Carry the correct base with the row.
def _rows(servers: dict, src: _Src) -> Iterator[dict]:
    for name, entry in servers.items():
        if not _looks_like_qa_server(str(name)):
            continue
        yield {
            "scope": src.scope,
            "name": str(name),
            "command": _entry_command(entry),
            "config": str(src.path),
            "base": str(src.base or src.path.parent),
        }


def _scan_registrations(src: _Src, seen_paths: set, out: list[dict]) -> None:
    """Append the qa-server registrations of one config file to ``out``."""
    path = src.path
    try:
        rp = path.resolve()
        if rp in seen_paths or not path.is_file():
            return
        seen_paths.add(rp)
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        servers = (data or {}).get("mcpServers") or {}
        if not isinstance(servers, dict):
            return
        out.extend(_rows(servers, src))
    except _RECOVERABLE:
        logger.debug("could not scan %s for MCP registrations", path, exc_info=True)


def _script_token(raw: str) -> str:
    cleaned = raw.replace("${workspaceFolder}/", "").replace("${workspaceFolder}", "")
    tokens = [t for t in cleaned.split() if t]
    found = (t for t in reversed(tokens) if t.endswith((".py", ".sh")))
    return next(found, "")


# Grouping on the raw command over-counts, and a warning that inflates its own
# number is worse than none: the same dev checkout is spelled
# ``.venv/bin/python mcp_server.py`` in one config and
# ``${workspaceFolder}/.venv/bin/python ${workspaceFolder}/mcp_server.py`` in
# another, which naive grouping reports as two separate servers.
#
# So resolve to the directory: take the last `.py`/`.sh` token (the server
# script, not the interpreter), strip `${workspaceFolder}` placeholders, resolve
# a relative path against the CONFIG's own directory -- which is what a
# project-scope relative path actually means -- and return its parent. Falls back
# to the raw command when no script token is present, so unrelated servers are
# never silently merged. Never raises.
def install_target(command: str, base: str | Path) -> str:
    """The install DIRECTORY a registration points at, for grouping."""
    try:
        raw = str(command or "").strip()
        script = _script_token(raw)
        if not script:
            return raw
        p = Path(script)
        if not p.is_absolute():
            p = Path(base) / p
        return str(p.resolve().parent)
    except _RECOVERABLE:
        logger.debug("install_target failed for %r", command, exc_info=True)
        return str(command or "")

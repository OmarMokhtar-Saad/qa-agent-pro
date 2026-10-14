"""The MCP-client rows of the machine report: installed, and is THIS install registered."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, NamedTuple

from tools import client_registry, machine_report_core
from tools.machine_report_core import _PROBE_ERRORS, Row, logger
from tools.process_env import home_dir

_NOT_INSTALLED = "Not installed on this machine."
_WIZARD_HINT = "Let the setup wizard add the qa-agents entry."
_CURRENT = "Registered, pointing at this install."
_ANOTHER = "Registered, but pointing at another install: "
_NO_ENTRY_MCP = "Installed, with no qa-agents entry in its MCP config."
_NO_ENTRY_CONFIG = "Installed, with no qa-agents entry in its config."
_STALE_FIX_CODE = "Re-register this install, or remove the stale entry."
_STALE_FIX = (
    "Re-register this install, or remove the stale entry -- "
    "two installs live at once is how work stages on one "
    "and finalizes on the other."
)


class _Client(NamedTuple):
    """One client whose config lives at a known path, and its per-client wording."""

    label: str
    config_path: Path
    no_entry: str
    stale_fix: str


class _Reg(NamedTuple):
    """What is registered: entries per config path, and THIS install's root."""

    by_config: dict
    mine: str


def _registered_targets(entries: list, cfg: Path) -> list:

    return [
        client_registry.install_target(
            str(e.get("command") or ""), e.get("base") or cfg.parent
        )
        for e in entries
    ]


def _registration_row(client: _Client, reg: _Reg) -> Row:
    """The row for one installed client: no entry, current, or another install."""
    cfg = client.config_path
    entries = reg.by_config.get(str(cfg)) or []
    if not entries:
        return Row(client.label, "fail", client.no_entry, _WIZARD_HINT, str(cfg))
    targets = _registered_targets(entries, cfg)
    if reg.mine in targets:
        return Row(client.label, "ok", _CURRENT, "", str(cfg))
    stale = _ANOTHER + ", ".join(t for t in targets if t)
    return Row(client.label, "warn", stale, client.stale_fix, str(cfg))


def _standard_row(label: str, config_path: Path, reg: _Reg) -> Row:
    """The row for one client whose config lives at a known path."""
    if not config_path.parent.exists():
        return Row(label, "off", _NOT_INSTALLED, "", str(config_path))
    client = _Client(label, config_path, _NO_ENTRY_MCP, _STALE_FIX)
    return _registration_row(client, reg)


def _claude_code_config(home):
    """The path of ``~/.claude.json`` under ``home`` (or the real home)."""
    # Claude Code is NOT in `default_targets` -- it is registered through
    # `claude mcp add` rather than by editing a file this server knows the
    # path of, which is exactly why the loop above cannot see it. Decision
    # (6) names it as one of the three clients the wizard registers, so it
    # is detected HERE, read-only: the CLI on PATH is what proves it is
    # installed, and `~/.claude.json` is the config `discover_registrations`
    # already scans. Detection only -- the WRITE is the desktop app's, with
    # a backup beside the file.
    return Path(home) / ".claude.json" if home else home_dir() / ".claude.json"


def _claude_code_row(home, reg: _Reg) -> Row:
    """The Claude Code row, detected read-only (it is not in ``default_targets``)."""
    code_config = _claude_code_config(home)
    code_present = bool(shutil.which("claude")) or code_config.is_file()
    if not code_present:
        return Row("Claude Code", "off", _NOT_INSTALLED, "", "")
    client = _Client("Claude Code", code_config, _NO_ENTRY_CONFIG, _STALE_FIX_CODE)
    return _registration_row(client, reg)


def _discover(home) -> list:

    try:
        return list(client_registry.discover_registrations(home) or [])
    except _PROBE_ERRORS as exc:
        logger.info("machine_report: discovery failed: %s", exc)
        return []


def _group_by_config(found: list) -> dict:
    by_config: dict[str, list[Any]] = {}
    for entry in found:
        if not isinstance(entry, dict):
            continue
        by_config.setdefault(str(entry.get("config") or ""), []).append(entry)
    return by_config


# Which MCP clients are installed, and whether THIS install is registered.
#
# Read-only by construction: it calls ``discover_registrations``,
# ``default_targets`` and ``install_target`` and never ``register_entry`` /
# ``register_client`` / ``register_all``. Writing a client config is the
# WIZARD's job, with a backup beside the file; the server does not edit files
# outside its own tree on this path.
#
# "Current" is a claim about WHICH INSTALL the entry points at, not about the
# entry merely existing: a registration left behind by an older install is
# exactly the failure that made a prep stage on one install and finalize on
# another. It is decided by ``client_registry.install_target`` -- the same
# producer the doctor's split-install warning uses -- compared against
# ``install_dir()``.
def client_items(home=None) -> list:
    rows = []
    try:
        found = _discover(home)
        by_config = _group_by_config(found)
        reg = _Reg(by_config, str(machine_report_core.install_dir()))
        for label, config_path, _dir in client_registry.default_targets(home):
            rows.append(_standard_row(label, Path(config_path), reg))
        rows.append(_claude_code_row(home, reg))
    except _PROBE_ERRORS as exc:
        rows.append(Row("mcp_clients", "undetermined", str(exc)))
    return rows

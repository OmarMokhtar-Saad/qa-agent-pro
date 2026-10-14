"""Read-merge-write of one MCP config entry (split out of client_registry.py)."""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from tools.clireg_consts import _RECOVERABLE, ADDED, ERROR, PRESENT, SKIPPED, UPDATED
from tools.clireg_lock import _atomic_write as atomic_write
from tools.clireg_lock import _lock as lock_path
from tools.clireg_lock import _unlock
from tools.clireg_spec import ClientOpts, EntrySpec

logger = logging.getLogger("qa_agents.client_registry")


# ``entry`` is the server object installed verbatim: a stdio
# ``{"command": ...}`` for this server, or a remote
# ``{"type": "http", "url": ...}`` for a hosted one such as Atlassian.
# Generalized 2026-08-04 -- the body hardcoded the stdio shape, so NO caller
# could add the Atlassian entry and every tester was told to hand-edit
# ``mcpServers`` JSON, which is the step non-technical testers fail at.
#
# ``insert_only=True`` (the startup pass, and every THIRD-PARTY entry): add the
# entry when absent and leave an existing one EXACTLY as the tester left it.
# ``insert_only=False`` (``connect.sh``, for OUR OWN entry): also repair an entry
# whose value no longer matches, which is what makes re-running it useful after
# the install moves.
#
# ``require_dir`` is the client's own directory. When given and missing, this is
# a skip, not an error -- writing a config for an editor the tester does not have
# installed would be creating state for a product that is not there.
#
# "Never raises" is a CONTRACT here -- the launcher's startup pass calls
# through this function, and the shape it replaced
# (``{"command": str(start_command)}``) could not fail. So a bad entry is
# an error RETURN, not a TypeError escaping into startup.
def register_entry(spec: EntrySpec) -> tuple[str, str]:
    """Ensure ``server_name`` maps to ``entry`` in an MCP config. (status, detail).

    Never raises.
    """
    path = Path(spec.config_path)
    if not isinstance(spec.entry, dict):
        return ERROR, "entry must be a JSON object"
    if spec.require_dir is not None and not Path(spec.require_dir).is_dir():
        return SKIPPED, "client not detected"
    spec = dataclasses.replace(spec, config_path=path, entry=dict(spec.entry))
    fd = lock_path(path)
    try:
        return register_locked(spec)
    except _RECOVERABLE as exc:
        logger.debug("register_entry failed for %s", path, exc_info=True)
        return ERROR, str(exc)
    finally:
        _unlock(fd)


# .bak ONLY now that a real write is about to happen.
def _backup(path: Path, original: str | None) -> None:
    if original is None:
        return
    try:
        atomic_write(Path(str(path) + ".bak"), original)
    except OSError:
        logger.debug("could not write a .bak", exc_info=True)


def register_locked(spec: EntrySpec) -> tuple[str, str]:
    """The read-merge-write of ``register_entry``, run while the lock is held."""
    path = spec.config_path
    cfg, original, failure = _read_config(path)
    if failure is None:
        status, failure = _place_entry(cfg, spec)
    if failure is not None:
        return failure
    payload = json.dumps(cfg, indent=2) + "\n"
    if original is not None and payload == original:
        return PRESENT, "already up to date"
    _backup(path, original)
    atomic_write(path, payload)
    return status, str(path)


# Never clobber something we cannot parse.
def _read_config(
    config_path: Path,
) -> tuple[dict, str | None, tuple[str, str] | None]:
    """(cfg, original text, failure): ``failure`` is a ready (status, detail) or None."""
    if not config_path.is_file():
        return {}, None, None
    original = None
    try:
        original = config_path.read_text(encoding="utf-8")
        stripped = original.strip()
        cfg = json.loads(stripped) if stripped else {}
    except (OSError, ValueError) as exc:
        msg = f"existing config is not readable JSON ({exc})"
        return {}, original, (ERROR, msg)
    if not isinstance(cfg, dict):
        return cfg, original, (ERROR, "existing config root is not a JSON object")
    return cfg, original, None


def _existing_outcome(existing: object, spec: EntrySpec) -> tuple[str, str] | None:
    """The PRESENT result when ``existing`` must be left alone, else None."""
    if existing is None:
        return None
    if spec.insert_only:
        return PRESENT, "already registered; left untouched"
    if existing == spec.entry:
        return PRESENT, "already registered with this command"
    return None


def _place_entry(cfg: dict, spec: EntrySpec) -> tuple[str, tuple[str, str] | None]:
    """Set ``spec.entry`` in ``cfg``: (status, failure); ``failure`` is an early (status, detail)."""
    servers = cfg.get("mcpServers")
    if servers is None:
        servers = {}
        cfg["mcpServers"] = servers
    if not isinstance(servers, dict):
        return "", (ERROR, "existing mcpServers is not a JSON object")
    existing = servers.get(spec.server_name)
    kept = _existing_outcome(existing, spec)
    if kept is not None:
        return "", kept
    servers[spec.server_name] = spec.entry
    return (ADDED if existing is None else UPDATED), None


# A thin delegate to ``register_entry`` so the merge/lock/backup logic has
# exactly one implementation. Signature and semantics are unchanged from when
# this function held that logic itself. Never raises.
def register_client(
    config_path: Path, start_command: str, opts: ClientOpts | None = None
) -> tuple[str, str]:
    """Ensure THIS server (a stdio ``command`` entry) is registered."""
    o = opts or ClientOpts()
    entry = {"command": str(start_command)}
    spec = EntrySpec(config_path, o.server_name, entry, o.insert_only, o.require_dir)
    return register_entry(spec)


def _register_locked(spec: EntrySpec) -> tuple[str, str]:
    """Legacy name of :func:`register_locked` (WX removes it)."""
    return register_locked(spec)

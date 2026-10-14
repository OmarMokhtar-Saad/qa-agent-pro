"""Register over every known target (split out of client_registry.py)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tools.clireg_consts import _RECOVERABLE, ATLASSIAN_ENTRY, ATLASSIAN_NAME, ERROR
from tools.clireg_spec import ClientOpts, EntrySpec
from tools.clireg_targets import atlassian_targets, default_targets
from tools.clireg_write import register_client, register_entry


def _guarded(fn: Callable, *args: object) -> tuple[str, str]:
    try:
        return fn(*args)
    except _RECOVERABLE as exc:  # pragma: no cover - the registrars never raise
        return ERROR, str(exc)


# Never raises: one client's failure must not stop the others, and must never
# stop a server from starting.
def register_all(
    start_command: str,
    *,
    home: Path | None = None,
    insert_only: bool = False,
) -> list[tuple[str, str, str]]:
    """Run ``register_client`` over ``default_targets``. Returns per-client results."""
    results: list[tuple[str, str, str]] = []
    for label, path, need in default_targets(home):
        opts = ClientOpts(insert_only=insert_only, require_dir=need)
        status, detail = _guarded(register_client, path, start_command, opts)
        results.append((label, status, detail))
    return results


# ``insert_only`` defaults to **True**, unlike ``register_all``: this is a THIRD
# PARTY's entry and an existing one may be authorized or hand-tuned. Repairing
# our own stale command is a different situation from rewriting someone else's.
#
# This writes the entry; it does NOT authorize it. OAuth happens in the editor,
# so callers must phrase the result as "configured, one click left" and never as
# "Jira is connected" -- the same gap tools/jira_mcp.connect_hint_line() is
# already careful about.
#
# Never raises: one client's failure must not stop the others.
def register_atlassian(
    *, home: Path | None = None, insert_only: bool = True
) -> list[tuple[str, str, str]]:
    """Insert the hosted Atlassian entry for each file-configured client."""
    results: list[tuple[str, str, str]] = []
    for label, path, need in atlassian_targets(home):
        spec = EntrySpec(path, ATLASSIAN_NAME, ATLASSIAN_ENTRY, insert_only, need)
        status, detail = _guarded(register_entry, spec)
        results.append((label, status, detail))
    return results

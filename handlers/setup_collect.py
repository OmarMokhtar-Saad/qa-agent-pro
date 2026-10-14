"""The qa-doctor checks: gathers one _SetupRun (moved from setup_check)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import cast

from handlers import (
    _common,
    host_check,
    mobile_gate,
    setup_editor_note,
    setup_env,
    setup_export_dir,
    setup_findings,
    setup_jira,
    setup_reload_gate,
    setup_reload_marker,
    setup_reload_report,
    setup_state,
)
from tools import updater

logger = logging.getLogger("tools.mcp_handlers")


def _record_reload_outcome(run: setup_state._SetupRun) -> str:
    run.reload_note, action = setup_reload_report.reload_outcome(
        setup_reload_marker.consume_reload_marker()
    )
    return action


def _collect_environment(
    run: setup_state._SetupRun, install_dir: Path, reload_action: str
) -> None:
    run.restart_note = setup_editor_note._editor_restart_note(install_dir)
    run.tool_paths = host_check.optional_tool_paths()
    run.mobile_state = mobile_gate.mobile_lane_state()
    run.limited += host_check.tooling_gaps(run.tool_paths)
    setup_reload_gate._reload_and_drift_items(reload_action, run.recommended)
    run.py_version = sys.version.split()[0]
    run.py_ok = sys.version_info >= (3, 10)
    if not run.py_ok:
        run.blockers.append(
            f"Upgrade Python to 3.10 or newer (currently {run.py_version})."
        )


# Bound BEFORE the edition gate: the advisory below reads it on
# every edition, and a full checkout never runs the check at all.
async def _setup_collect(
    progress: _common.ProgressCb, workspace_roots: list[Path] | None, fix: bool
) -> "setup_state._SetupRun | str":
    """Run the checks; returns the gathered run, or a reply that ends it early."""

    update_status, reload_reply = await setup_reload_gate.run_reload_gate(progress)
    if reload_reply is not None:
        return reload_reply
    run = setup_state._SetupRun(workspace_roots=workspace_roots, fix=fix)
    reload_action = _record_reload_outcome(run)
    await _common.emit(progress, "🔎 Validating the environment…")
    run.app_version = cast("str", updater._local_version(updater._INSTALL_DIR))
    _collect_environment(run, updater._INSTALL_DIR, reload_action)
    setup_findings._standing_findings(run, update_status)
    await _setup_collect_jira(run, updater._INSTALL_DIR)
    return run


# Jira is never an unsettled question here: it is checked on demand.
# KEYWORD, and pinned at this call site by its own test: the
# parameter has a default, so nothing about the function alone can
# catch this argument being dropped.
async def _setup_collect_jira(run: setup_state._SetupRun, install_dir: Path) -> None:
    """Jira, export-dir and env-heal findings, then the overall verdict."""
    run.view = await setup_jira._load_verdict_view()
    state = setup_jira._atlassian_verdict_state(run.view)
    run.jira_used, run.atlassian_lines = await setup_jira._atlassian_part(run)
    run.optional.append(setup_env._CONNECT_SCRIPT_HINT)
    run.jira_status_line = host_check.jira_status_text(state, run.view)
    run.export_line = setup_export_dir._export_dir_line(run.recommended)
    run.heal_lines = await setup_env._env_heal_lines(
        install_dir, run.fix, run.recommended
    )
    setup_env._flag_expiry_item(run.optional)
    setup_jira._jira_state_advisories(run, state)
    run.verdict = host_check.overall_verdict(
        host_check.VerdictCounts(len(run.blockers), len(run.recommended), 0),
        limited=tuple(run.limited),
    )

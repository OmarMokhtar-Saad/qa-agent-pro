"""The qa-doctor handler: collects a run and renders the report (moved from setup_check)."""

from __future__ import annotations

import logging
from pathlib import Path

from handlers import (
    _common,
    _lane,
    mobile_gate,
    setup_cache,
    setup_collect,
    setup_gates,
    setup_mobile_off,
    setup_report,
    setup_state,
)

logger = logging.getLogger("tools.mcp_handlers")


async def _report_text(run: setup_state._SetupRun) -> str:
    lines = setup_report._setup_report_head(run.head())
    lines += await setup_gates.setup_gate_lines(run.mobile_state)
    lines += await _setup_mobile_lines(run.mobile_state, run.tool_paths)
    lines += await setup_report._unfinished_and_ac_lines()
    lines += setup_report._action_item_lines(
        run.blockers, run.recommended, run.optional
    )
    return "\n".join(lines)


# [reload_note: why it is read AFTER the reload gate]
# Report the PREVIOUS reload's outcome ("" / "" when there was none).
# Deliberately AFTER the block above: a call that schedules a new reload
# has already returned, and its marker overwrites the old one, so the
# next settled call always reports the most recent reload.
#
# [the `limited` list]
# The FOURTH list, and it is not a fourth severity: `limited` names
# what this machine CANNOT DO and feeds no count in `overall_verdict`
# (see there). Everything in it is true of the machine, actionable
# nowhere in this report, and invisible in the headline until now.
#
# [_tool_paths / _mobile_state, read once]
# The lane's THREE states, from the one producer. Read once: the
# Feature-gates row, the mobile section and the headline all branch on
# this same value, so they cannot describe the lane three ways.
#
# [the lane in the headline (orphan comment after `limited +=`)]
# The lane reaches the HEADLINE only where this machine could plausibly
# run it today: modules on disk, the switch off, and `adb` already
# here. Two reasons for that bound. Where `adb` is missing the gap
# above already names Android tooling, so exactly one Android-shaped
# phrase can ever appear; and where the lane is merely off on a machine
# with no Android tooling at all, a permanent headline suffix on every
# report forever is the nag-with-no-exit this function was corrected
# for on 2026-09-01. The DISCLOSURE itself is unconditional -- it is in
# the Feature-gates row and in the section below on every install.
#
# [the mobile lane section, on branch]
# The mobile lane's own section: SDK, IME and emulator state. Empty
# (not a "disabled" line) when the lane is off -- the gate row above
# already said so, and a second telling is noise on every install that
# will never use it.
# BRANCH ON THE STATE, never on the section being empty. "An empty list
# means the lane is off" is an inference, and a future early return
# inside `_mobile_doctor_section` would silently turn a running lane
# into an off-lane disclosure. `_mobile_doctor_section` keeps its own
# `mobile_lane_enabled()` guard as well: two independent checks of the
# same state, neither of which is load-bearing alone.
#
# [the mobile lane section, off branch]
# The lane is present-but-off, or not in this build at all. It used
# to render as one blank checkbox that named no setting and no
# capability; a tester with an Android phone attached read that and
# learned nothing they could act on.
#
# [the attached-device note in the off branch]
# A tester with a phone plugged in and the flag off used to read
# a block that never mentioned their device. This names it --
# but only from an adb server that is ALREADY up, so the
# disclosure still starts nothing.
# Machine-readiness report for tester onboarding: environment, LLM
# backend auth, integrations, CLI tooling, and feature gates — summarised
# into an overall verdict plus concrete action items. Never raises.
#
# **Read-only by default** (2026-09-25, P2-10): pass ``fix=True`` to repair
# superseded defaults in the install's own `.env` and write a missing
# `atlassian` MCP entry to the editor's config. With ``fix=False`` neither
# write runs; both are reported as "not checked, call with fix=true"
# instead. Neither touches the unattended startup pass.
#
# ``workspace_roots`` is the tester's OPEN workspace folder(s) as reported by
# the MCP ``roots`` capability. It is resolved in ``mcp_server.qa-doctor``
# (that is where the client Context lives, and tools/jira_mcp.py makes no
# protocol call of its own) and is forwarded ONLY to ``connect_hint_line``, so
# the on-disk `atlassian` check looks in the project the tester actually has
# open instead of guessing. ``None`` = a client without ``roots`` support: the
# hint falls back to its previous candidate chain.
async def handle_setup_check(
    *,
    progress: _common.ProgressCb = None,
    workspace_roots: list[Path] | None = None,
    fix: bool = False,
) -> str:
    """Machine-readiness report for tester onboarding; never raises."""
    await _common.audit("mcp_setup_check")
    key = setup_cache.roots_key(workspace_roots)
    cached = setup_cache.lookup(fix, key)
    if cached is not None:
        return cached
    try:
        run = await setup_collect._setup_collect(progress, workspace_roots, fix)
        if isinstance(run, str):
            return run
        return await _cached_report(run, fix, key)
    except Exception as exc:
        logger.exception("handle_setup_check failed")
        return f"⚠️ Setup check failed: {exc}"


async def _cached_report(run, fix: bool, key: str) -> str:
    """Render the report and keep it for the next call (fix runs are never kept)."""
    report = await _report_text(run)
    setup_cache.store(fix, report, key)
    return report


async def _setup_mobile_lines(mobile_state: str, tool_paths: dict) -> list[str]:
    """The mobile section: the doctor section when ON, the disclosure when OFF."""
    if mobile_state == "on" and _lane.mobile_lane is not None:
        mobile_lines = await _lane.mobile_lane.doctor_section()
        return ["", *mobile_lines] if mobile_lines else []
    return [
        "",
        *mobile_gate.mobile_lane_disclosure(mobile_state, tool_paths.get("adb")),
        *await setup_mobile_off.mobile_off_attached_note(),
    ]

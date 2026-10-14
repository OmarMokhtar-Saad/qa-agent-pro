"""Jira connection check and autofix for the host-check report."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from tools import client_registry, settings_port
from tools.jira_mcp import atlassian_entry_configured

logger = logging.getLogger("tools.mcp_handlers")

_RESTART_NOTE = (
    "_One step is still yours: **restart your editor**, then paste "
    "a ticket URL \u2014 the first one opens the Atlassian sign-in "
    "prompt. I can write the entry, but I cannot sign you in, and "
    "I cannot see from here whether it worked._"
)


# qa-doctor checks and mentions Atlassian/Jira only then. Any one is enough: a
# stored Atlassian verdict (so a known-broken connection is never hidden), a
# non-empty ``settings.jira_base_url`` or ``settings.jira_ac_field``, or an
# ``atlassian`` entry on disk for a file-configured client. A failed lookup
# answers False, which only ever removes Jira lines.
def jira_in_use(
    workspace_roots: list | None = None, verdict_view: object = None
) -> bool:
    """True when Jira is ALREADY part of this install. Read-only, never raises."""
    with never_raise(logger, "jira-in-use check failed", exc_info=True):
        if verdict_view:
            return True
        if str(settings_port.get().jira_base_url or "").strip():
            return True
        if str(settings_port.get().jira_ac_field or "").strip():
            return True

        return atlassian_entry_configured(workspace_roots)
    return False


# Design notes for atlassian_autofix (kept here so the function stays short).
#
# WHY THIS LIVES IN THE SETUP CHECK AT ALL. v1.42.0 taught connect.sh/.ps1 to
# write the entry, but connect runs only from install.ps1 or by hand, the
# launcher's startup pass registers THIS server and nothing else, and the updater
# never calls connect -- so an install that AUTO-UPDATED into v1.42.0 received
# the code and none of the behaviour. Observed on a Windows machine that went
# 1.41.2 -> 1.42.0 and still reported "Not connected" while telling the tester to
# hand-edit ~/.cursor/mcp.json. Every UPGRADING install was unreachable; only
# fresh ones were fixed, which is the opposite of where the users are.
#
# WHY THIS IS ALLOWED TO WRITE OUTSIDE THE INSTALL DIR, when
# QA_AUTO_REGISTER_CLIENTS defaults OFF for doing the same class of thing: that
# flag guards an UNATTENDED startup pass -- "a server that inserts itself into
# other editors' configs whenever it starts" -- and that pass is untouched here.
# This runs only because a tester invoked this tool BY NAME, and the write is
# disclosed in the very response they are already reading. Secondarily it is the
# same disclosure shape QA_ENV_SELFHEAL_ENABLED already uses when this tool
# repairs the install's own .env, though that file is INSIDE the install dir, so
# that precedent supports the shape, not the location.
#
# It writes the entry; it does NOT authorize it. OAuth is the tester's click and
# cannot be observed from a stdio subprocess, so nothing here may report Jira as
# connected -- the same line tools/jira_mcp.connect_hint_line() already walks.
#
# No pre-check on purpose: register_atlassian() already returns PRESENT for an
# existing entry, so calling it unconditionally is both idempotent and free of a
# check-then-act race. Never raises -- a failure costs one advisory line, never
# the report.
#
# An advisory is NOT verdict-neutral: the verdict below reads "Ready, with
# warnings" whenever `recommended` is non-empty. That is intended here, because
# an unwritable entry costs the tester Jira grounding and comes with a concrete
# paste-this action. It is never a BLOCKER -- a Jira convenience cannot make an
# otherwise healthy install report "Not ready".
#
# QA_REGISTER_ATLASSIAN_MCP was DELETED on 2026-08-13 (flag-surface reduction,
# batch 6) and hardcoded ON, so writing itself is unconditional once invoked.
# P2-10 (2026-09-25) added a caller-side gate instead: qa-doctor only passes
# fix=True when the tester asked for repairs, so a plain qa-doctor call never
# reaches register_atlassian at all.
async def atlassian_autofix(fix: bool = False) -> tuple[list[str], list[str]]:
    """Write the hosted `atlassian` MCP entry when missing. (report_lines, advisories)."""
    with never_raise(logger, "atlassian autofix failed - skipping it", exc_info=True):
        if not fix:
            return [], [
                "Jira MCP entry not checked -- call qa-doctor with fix=true "
                "to write it if missing."
            ]

        # to_thread because it takes a file lock, exactly as heal_env is called.
        results = await asyncio.to_thread(client_registry.register_atlassian)
        return _atlassian_report(results)
    return [], []


def _added_lines(label: str, detail: str, first: bool) -> list[str]:
    """Report lines for one ADDED entry; the heading only for the first."""
    # Header hoisted out of the per-client body: one target exists
    # today, but a second would otherwise repeat the heading.
    head = ["### Jira connection configured", ""] if first else []
    return head + [
        f"- Added the `atlassian` MCP entry for {label} — "
        f"`{detail}`. Your previous file was backed up alongside it "
        "as `.bak`, and any other MCP servers in it were left alone."
    ]


def _error_advisory(label: str, detail: str) -> str:
    """The advisory for one entry that could not be written."""
    return (
        f"Could not add the `atlassian` MCP entry for {label} "
        f"({detail}). Add it under `mcpServers` yourself, then restart: "
        '`"atlassian": {"type": "http", "url": '
        '"https://mcp.atlassian.com/v1/mcp/authv2"}`'
    )


def _atlassian_report(results: list) -> tuple[list[str], list[str]]:
    """Fold register_atlassian results into (report_lines, advisories)."""

    lines: list[str] = []
    advisories: list[str] = []
    for label, status, detail in results:
        if status == client_registry.ADDED:
            lines += _added_lines(label, detail, not lines)
        elif status == client_registry.ERROR:
            advisories.append(_error_advisory(label, detail))
    if lines:
        lines += ["", _RESTART_NOTE, ""]
    return lines, advisories

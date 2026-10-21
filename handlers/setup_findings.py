"""qa-doctor's standing findings and client-registry warnings (moved from setup_check)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config.settings_guard import never_raise
from handlers import host_check, setup_state, setup_update_idle
from tools import client_registry
from tools import host_llm as _host_llm

logger = logging.getLogger("tools.mcp_handlers")


def _llm_disclosure(run: setup_state._SetupRun) -> None:

    _server_llm_note, _server_llm_degraded = _host_llm.disclosure_state()
    if _server_llm_note and _server_llm_degraded:
        run.recommended.append(_server_llm_note)
    elif _server_llm_note:
        run.optional.append(_server_llm_note)


# Host-boomerang migration disclosure (lands in PHASE 1, with the kill
# switch itself). QA_SERVER_LLM_ENABLED=false while ledger rows are still
# unmigrated turns the TICKET-7154 ambiguity gate, bug reports, coaching,
# vision grounding and the eval harness OFF at once -- an operator must
# not have to discover that from behaviour, so it is reported here and in
# a one-time startup WARNING. It also changes what "backend broken" means:
# with the server LLM retired, test-case generation runs on the tester's
# own chat model, so an unusable backend is no longer a blocker.
# Phase-6 preparation (2026-08-02): the CALM branch became
# reachable in production for the first time when residue R4
# emptied UNMIGRATED_PATHS, and it is INFORMATION, not an action
# item. Left in `recommended` it would make every correctly
# retired install report "Ready, with warnings" forever -- the
# verdict below is derived from that list being non-empty -- which
# trains an operator to ignore the one list meant to demand
# attention. `optional` is where a true, no-action-needed
# statement belongs. Both degraded branches are unchanged and
# still land in `recommended`.
# 2026-08-15 (dead-code deletion batches D2 and D3): the three
# per-mode entries that stood here -- `maestro_healer.classify`,
# `maestro_explorer.decide` and `web_runner.verify` -- were DELETED
# with the modules and the modes they disclosed (tools/maestro_*.py
# in D2, tools/web_runner.py in D3). Naming a loss for a mode that
# no longer exists is the same dishonesty as hiding a real one,
# which is this block's whole discipline. Their ledger rows are
# HISTORY and stay in docs/LLM_MIGRATION_INVENTORY.md and in
# host_llm.LEDGER_IDS (ids never leave it; the set is pinned at 24
# in five test files), so an allow-list typo on any of them is
# still detectable.
# Residue sub-phase R3: `image_description.describe_images` is a terminal
# `disabled (disclosed)` row, so it has LEFT UNMIGRATED_PATHS and the
# generic disclosure line above no longer names it -- but its surviving
# caller is TESTER-FACING (the `mobile` / `jira_mobile` modes of
# qa_feature_analysis describe captured device screens through this
# server's own ask_vision), so the 5b/5c convention applies: name the
# mode and the exact allow-list id HERE rather than let a tester discover
# it standing at a device. Deliberately NOT inside the
# `not test_cases_only()` block above, on the grounds that the dist
# edition ships feature_analysis.py AND device_manager.py so the loss was
# just as real there.
# CORRECTION (2026-08-03): that rationale no longer holds. The dist does
# not REGISTER qa_feature_analysis / qa_submit_feature_analysis at all,
# so naming a mode a dist tester cannot invoke would invent a loss nobody
# suffers -- the precise thing this module's disclosure discipline
# forbids. It therefore moves INSIDE the edition gate; the full edition
# is byte-identical. The sibling vision row
# `ui_extractor.describe_via_vision` still gets NO item, deliberately:
# it is `migrated` (the rendered page screenshot rides to the host's own
# model through IMAGE_JOB on the only route that reaches it), so an item
# would invent a loss no tester suffers.
# 2026-08-15: the per-mode vision-loss item was DELETED, not disabled.
# qa_feature_analysis's mobile modes no longer need a server vision
# call at all -- the captured screens ride to the tester's own
# multimodal model as MCP image content -- so there is no loss to
# disclose, and QA_SERVER_LLM_ALLOW=image_description.describe_images
# would restore nothing (that caller is gone). Claiming a loss no
# tester suffers is the same dishonesty as hiding a real one.
# Phase 5d: the Maestro step-translation flag was already INERT on
# the MCP surface -- its only caller was the retired Chainlit export
# path, so qa-doctor had to report the FLAG ITSELF as having no
# effect. On 2026-08-13 QA_MAESTRO_TRANSLATE_ENABLED was DELETED and
# hardcoded OFF (flag-surface reduction, batch 8a), so there is no
# longer a configuration surprise to disclose, and that advisory is
# gone with it. tools.maestro_exporter.translate_enabled() is the
# seam; re-wiring the export path is still a separate plan.
# 2026-08-16 (dead-code deletion P2-G2b): the LLM-backend BLOCKER, the
# Environment backend line and both ANTHROPIC_API_KEY placeholder items
# stood here. All three reported on a capability this product no longer
# has -- P2-G2c deletes `llm.py`'s coroutines and all three backends, so
# nothing in the tree can reach one. The blocker was the harmful part:
# it told a tester whose Claude CLI session had expired that "nothing
# generates without it" while everything generated fine, because the
# generation runs in their own chat. Reporting a fault that cannot
# affect any tester flow is the same over-claim this function's
# disclosure discipline forbids in the other direction.
#
# The host-boomerang migration disclosure above is a DIFFERENT seam and
# is untouched: it reports what this server does not do, not whether a
# backend is reachable.
# D4 follow-up (2026-08-25): the tester-facing half of the
# "rate-limited" status. "" on every other status, so a healthy
# run is byte-identical.
def _standing_findings(run: setup_state._SetupRun, update_status: Any) -> None:
    _llm_disclosure(run)
    if run.restart_note:
        run.recommended.append(
            "Quit and reopen the editor once so it reloads the agent's "
            "latest tool definitions."
        )

    _client_registry_warnings(run.workspace_roots, run.recommended)
    _rate_limit_note = host_check.update_rate_limit_advisory(update_status)
    if _rate_limit_note:
        run.recommended.append(_rate_limit_note)
    _deferred = setup_update_idle.deferred_update_advisory(update_status)
    if _deferred:
        run.recommended.append(_deferred)


# 2026-08-01: there is nothing Jira-shaped left for this server to
# check. Jira is read through the CALLING AGENT's own Atlassian MCP
# connection (OAuth), which a stdio subprocess cannot observe -- so
# printing "configured" or "verified" here would be a guess, and a
# confident wrong answer is worse than none. State what is true and
# point at the one place that gives real guidance.
# 2026-08-03: warn BEFORE a suite is built when more than one qa server is
# registered. The packaged install sits in the USER configs while the
# project's own .mcp.json registers a DEV checkout, so opening the repo puts
# two live servers in front of the agent -- which is how one real run
# prepared on v0.1.0 and finalized on v1.34.0. Recommended, not optional:
# the two installs have separate .env files, so the flags the suite was
# prepared under are not the flags it was finalized under. Silent when every
# client points at the SAME install, which is the normal case.
# Cursor-hardening v1.97.0 (item 5): warn when the tester's open
# workspace CONTAINS this server's own source tree -- that is what
# lets an agent import handlers/ directly or spawn its
# own copy of this server, bypassing the registered tool surface and
# its dispatch guard entirely (2026-09-25 Cursor audit).
def _client_registry_warnings(
    workspace_roots: list[Path] | None, recommended: list[str]
) -> None:
    with never_raise(logger, "split-server check failed", exc_info=True):
        _split = client_registry.split_server_warning(workspace_roots=workspace_roots)
        if _split:
            recommended.append(_split)
    with never_raise(logger, "workspace-contains-server check failed", exc_info=True):
        _ws_warning = client_registry.workspace_contains_server_warning(
            workspace_roots=workspace_roots
        )
        if _ws_warning:
            recommended.append(_ws_warning)

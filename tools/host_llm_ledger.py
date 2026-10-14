"""LEDGER_IDS of tools.host_llm (one home; split out of host_llm.py)."""

from __future__ import annotations

# Continued from host_llm_unmigrated.py: the per-row rationale of the design record on
# UNMIGRATED_PATHS carries on below.
#
#   The legacy call is NOT deleted (graph.py, evals/test_eval_goldens.py and
#   evals/test_terse_schemas_goldens.py all still reach
#   decompose_to_checklist) and is now scope-tagged, so
#   QA_SERVER_LLM_ALLOW=atomic_checklist.decompose revives exactly that one
#   path after the Phase-6 flip. One narrowing is accepted and DISCLOSED
#   rather than papered over: with the checklist host-authored, the Batch-3
#   mandated rule-pack lines are not interleaved into it, so they run in
#   prompt + advisory mode instead of being scored.
# maestro_exporter.translate left this tuple in the Phase-5d ops file that
# flipped its ledger row to `disabled (disclosed)` -- NOT `migrated`, and not
# because a fold was hard: the path has had no production caller since the
# Chainlit UI was retired (handlers/ calls
# generate_maestro_flows(suite) with no translations map), so building a
# boomerang for it would have been new tester-facing capability disguised as
# a migration. It stays in LEDGER_IDS and is scope-tagged, so
# QA_SERVER_LLM_ALLOW=maestro_exporter.translate still revives it for the
# eval harness. qa-doctor gained no allow-list item for it (no tester can
# reach it) but DOES now report QA_MAESTRO_TRANSLATE_ENABLED as inert.
# maestro_healer.classify and maestro_explorer.decide left this tuple in the
# Phase-5b ops file that flipped both ledger rows to `disabled (disclosed)`
# (ledger rule 4: same op, or the drift test fails). Both remain in
# LEDGER_IDS, so an allow-list typo on either is still detectable.
# 2026-08-15 (dead-code deletion batch D2): tools/maestro_healer.py and
# tools/maestro_explorer.py were DELETED, so nothing carries either scope tag
# any more and the per-mode qa-doctor warnings they earned were deleted with
# the modes. The ids STAY here -- ids never leave LEDGER_IDS (asserted in five
# test files and pinned at 24) and the drift test compares this frozenset to
# docs/LLM_MIGRATION_INVENTORY.md, so shrinking one without the other is the
# single edit that guard cannot see. The same holds for maestro_exporter.translate
# above, whose module went in the same batch.
# web_runner.translate left this tuple in the Phase-5d ops file that flipped
# its ledger row to `migrated` -- the ONLY genuinely migrated row in Phase 5.
# It was never loop-bound: every case was translated BEFORE anything
# executed, so the batch point already existed and mechanism B split it into
# qa_run_web_suite (envelope carrying every case) -> qa_submit_web_run
# (validate, then drive the browser), at ONE extra tool call for a whole run
# rather than one ask_json per case. TWO notes for whoever adds the NEXT
# batched kind here: that payload is deliberately FLAT, because _cap above
# nulls every field below _MAX_DEPTH and a per-step nesting layer would have
# silently emptied every action; and its system prompt is a DEDICATED one,
# because the legacy per-case prompt described the nested shape and prose
# beats a schema the host may never be shown.
# web_runner.verify left this tuple in the Phase-5c ops file that flipped its
# ledger row to `disabled (disclosed)` (ledger rule 4: same op, or the drift
# test fails). It remains in LEDGER_IDS, so an allow-list typo on it is still
# detectable -- and it is now an id operators actually type. qa-doctor
# gained a third per-mode item for it, guarded on web-run enabled AND dry-run
# off AND a non-zero vision budget, because only that combination loses
# anything. The sibling row web_runner.translate was still here when 5c
# landed and was what actually decided this tool's post-flip fate; sub-phase
# 5d migrated it, so qa_run_web_suite now survives the flip with only the
# visual-verify degradation described above.
# 2026-08-15 (dead-code deletion batch D3): tools/web_runner.py was
# DELETED, along with both MCP tools and the whole handler chain, so
# nothing carries either web scope tag any more and the qa-doctor item
# web_runner.verify earned was deleted with the tool. BOTH ids STAY in
# LEDGER_IDS -- ids never leave it (asserted in five test files and
# pinned at 24) and the drift test compares this frozenset to
# docs/LLM_MIGRATION_INVENTORY.md, so shrinking one without the other is
# the single edit that guard cannot see.
# 2026-08-15 (dead-code deletion batch D5): tools/comment_reconciler.py
# was DELETED, along with the whole amendment pipeline in mcp_handlers,
# the jira_mcp seam reads and the six QA_COMMENT_RECONCILE_* settings, so
# nothing carries the comment_reconciler.candidates scope tag any more.
# The id STAYS in LEDGER_IDS on the same terms as the maestro and web ids
# above -- ids never leave it (asserted in five test files and pinned at
# 24) and the drift test compares this frozenset to
# docs/LLM_MIGRATION_INVENTORY.md, so shrinking one without the other is
# the single edit that guard cannot see. Reviving that row is now a
# module rebuild against docs/RETIRED_CAPABILITIES.md section 4, not a
# seam flip.
#
# WHAT IS LEFT AFTER RESIDUE SUB-PHASE R4: NOTHING. This tuple is EMPTY.
# R1 closed
# rtm.acceptance_criteria and test_scenario_agent.jira_images (both
# `migrated`: the AC_JOB / IMAGE_JOB folds are live and the host route
# provably cannot reach either call). R2 closed the three
# test_scenario_agent.* rows -- server_fanout `migrated`, rewrite_vague and
# markdown `disabled (disclosed)`, the latter two only because R2 also built
# the submit-reply disclosure that makes that token true. R3 closed the two
# vision rows -- ui_extractor.describe_via_vision `migrated` (its only
# production caller already defers the Tier-3 screenshot to IMAGE_JOB) and
# image_description.describe_images `disabled (disclosed)` (its mobile
# Feature-Analysis caller is live, tester-facing and has no host analog),
# and it deleted NOTHING, which is the correction the master plan's
# "Phase 3 -- vision residue: delete the now-unreachable calls" needed.
# R4 closed the last row, atomic_checklist.decompose (`migrated`), by
# building CHECKLIST_JOB and widening the two gates described above.
#
# Phase 6's gate ("flip only when UNMIGRATED_PATHS is empty or every
# remaining row is disabled (disclosed)") is therefore SATISFIED, and
# tests/test_host_llm.py::test_ledger_markdown_and_UNMIGRATED_PATHS_cannot_drift
# re-asserts it mechanically on every run. Read the empty tuple correctly,
# though: TWELVE of the 24 ledger ids read `migrated`, ELEVEN read
# `disabled (disclosed)` and one reads `retired (no host analog)`. Flipping
# QA_SERVER_LLM_ENABLED is a real, NAMED capability change -- no server-side
# vague-step rewrite, no advisory LLM gap prose, no mobile Feature-Analysis
# vision, no mobile heal/explore triage, no web visual-verify adjudication,
# no eval judges, no LangGraph intent classification -- not an inert default
# flip. Phase 6 must carry that list into its sign-off.

# Ledger rows (docs/LLM_MIGRATION_INVENTORY.md) that have NO boomerang
# alternative yet. Phase 1 ships them all: this is the honest state of the
# migration on the day the kill switch appears. Phases 2-6 REMOVE entries as each
# row reaches `migrated`, and an empty tuple is what makes the Phase-6 flip safe.
# Keep in sync with the ledger -- it is the source of truth, this is its runtime
# projection.
# EVERY ledger id in docs/LLM_MIGRATION_INVENTORY.md, migrated or not. Two jobs:
# it validates QA_SERVER_LLM_ALLOW entries (an id that is not here allows
# NOTHING -- almost always a typo -- and the disclosure says so out loud instead
# of leaving the operator to wonder why their allow-listed path is still off),
# and tests/test_host_llm.py parses the ledger markdown and asserts this set
# equals the ledger's id column, so ledger/runtime drift is caught mechanically.
# Ids NEVER leave this set as they migrate -- only UNMIGRATED_PATHS shrinks.
LEDGER_IDS: frozenset = frozenset(
    {
        "test_scenario_agent.jira_images",
        "test_scenario_agent.server_fanout",
        "test_scenario_agent.rewrite_vague",
        "test_scenario_agent.markdown",
        "llm.warm_cache_prefix",
        "requirement_analyzer.ambiguity_gate",
        "rtm.acceptance_criteria",
        "rtm.nli_verdicts",
        "atomic_checklist.decompose",
        "comment_reconciler.candidates",
        "risk_scorer.llm_risk",
        "test_plan_report.build",
        "feature_analysis.report",
        "image_description.describe_images",
        "ui_extractor.describe_via_vision",
        "bug_report_agent.report",
        "exploratory_coach_agent.next_step",
        "maestro_exporter.translate",
        "web_runner.translate",
        "web_runner.verify",
        "maestro_healer.classify",
        "maestro_explorer.decide",
        "eval_runner.judge",
        "router.classify",
    }
)

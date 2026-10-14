"""API-test generation, tracker push and AC-anchoring fields.

Plain ``BaseModel`` mixin; ``config.settings.Settings`` composes it.
"""

from __future__ import annotations

from pydantic import BaseModel


class ApiPushSettings(BaseModel):
    # --- Mobile Device Testing (Maestro) -- DELETED 2026-08-15. ---
    # Batch 7 (2026-08-13) retired the feature and kept seven QA_MAESTRO_* tuning
    # fields as knobs that gated nothing. On 2026-08-15 (dead-code deletion batch
    # D2) the cluster itself was DELETED -- tools/maestro_runner.py,
    # maestro_healer.py, maestro_explorer.py, maestro_exporter.py,
    # handle_run_mobile_suite, the qa_run_mobile_suite tool and the qa_wizard
    # Mobile branch -- so all seven fields lost their last reader and went with
    # it. A stale QA_MAESTRO_* line in an existing .env is ignored (extra="ignore").
    # Device capture is untouched: qa_capture_screens / qa_list_devices /
    # tools/device_manager.py never depended on Maestro.
    #
    # QA_TEST_USER / QA_TEST_PASSWORD went the same way on 2026-08-15. Batch
    # D2 kept them because their last reader had become tools/web_runner.py's
    # seed_account field filler; dead-code deletion batch D3 deleted that
    # module, so both fields lost their only reader and went with it.

    # --- Web Suite Execution -- DELETED 2026-08-15 (batch D3). ---
    # Batch 6 (2026-08-13) deleted the two switches and hardcoded the runner
    # OFF, keeping three bounds as tuning knobs for whoever revived it in
    # code. D3 deleted the code they bounded -- tools/web_runner.py,
    # handle_run_web_suite / handle_submit_web_run, the qa_run_web_suite and
    # qa_submit_web_run tools and suite_store's web_runs table -- so the
    # three fields lost their last reader too. A stale QA_WEB_RUN_* or
    # QA_TEST_USER line in an existing .env is ignored (extra="ignore").
    # See docs/FEATURE_FLAGS.md.

    # LangSmith tracing is GONE. These two fields mirrored LANGCHAIN_API_KEY /
    # LANGCHAIN_PROJECT "for visibility" -- nothing in this tree ever read
    # them; the langsmith and langgraph packages read those variables straight
    # from the environment, on the graph.py path. Dead-code deletion Phase 2
    # batch P2-A (2026-08-15) deleted graph.py, router.py and langgraph.json
    # and dropped langchain-core / langgraph / langsmith /
    # langchain-anthropic from pyproject.toml and requirements.txt, so there is
    # no reader left inside OR outside this process. A stale LANGCHAIN_* line
    # in an existing .env is ignored (extra="ignore").

    # Web search grounding is GONE. QA_WEB_SEARCH_ENABLED was DELETED on
    # 2026-08-13 (flag-surface reduction, batch 6) and hardcoded OFF; on
    # 2026-08-15 (dead-code deletion, batch D1) tools/web_search.py itself was
    # deleted, along with the compliance-keyword scan in
    # agents/test_scenario_agent.py. No feature or ticket text can leave the
    # org for a third-party search API, and there is no module left to
    # re-enable. See docs/RETIRED_CAPABILITIES.md.

    # The structured coverage critic + bounded remediation loop, and the
    # QA_COVERAGE_REGEN_MAX_ROUNDS int field that bounded it, were DELETED on
    # 2026-08-16 (dead-code deletion P2-E1). The loop was the field's only
    # reader; a stale QA_COVERAGE_REGEN_MAX_ROUNDS= line in an existing .env
    # is inert (model_config uses extra="ignore").
    # Merging the critique + gap-fill generation into ONE ask_json call per
    # remediation round (instead of critique_coverage followed by a full
    # _generate_for_category pass) is UNCONDITIONAL since 2026-08-13
    # (flag-surface reduction, batch 8a): QA_COVERAGE_REGEN_MERGE_CALLS was
    # DELETED and hardcoded ON. This halves the call count of every legacy-critic
    # remediation round, and the merged call resolves to qa_llm_model, NOT
    # qa_classifier_model, because its output includes tester-facing generated
    # test cases and not just an internal critique. The checklist-driven branch
    # is unaffected, exactly as before: its "critique" is the deterministic
    # external matcher, so there is nothing there to merge.
    # See .claude/plans/plan-remediation-cap.md and docs/FEATURE_FLAGS.md.
    # Enterprise Feature Analysis Report. QA_FEATURE_ANALYSIS_ENABLED was
    # DELETED on 2026-08-14 (flag-surface reduction, batch 8c) and the
    # feature hardcoded ON -- the flag policy's "promote to default ON,
    # flag deleted" outcome for an experiment. The seams were deleted
    # (WD) and the ON path is inlined. The test-cases-only
    # EDITION gate is unchanged and still outranks them, so the public
    # distribution registers neither tool.

    # API test agent (chat-only): qa_prepare/submit/write_api_test. All OFF by
    # default; the write path is dry-run-first (qa_api_framework_write_dry_run).
    qa_api_test_enabled: bool = False
    # Kill switches for the LIVE pushers (2026-08-18). tools/testrail_pusher.py and
    # tools/xray_pusher.py had no caller at all; qa_push_suite is that caller. ON
    # permits a credentialed outbound WRITE to a system outside this org's control,
    # which nothing else in this tree does, so both default OFF forever and a real
    # push additionally requires apply=true on the call. A dry-run preview needs
    # neither flag. Flipping either one needs an MCP server restart.
    qa_testrail_push_enabled: bool = False
    qa_xray_push_enabled: bool = False
    qa_api_framework_write_enabled: bool = False
    qa_api_framework_path: str = ""
    qa_api_framework_write_dry_run: bool = True
    # The PUBLIC GitHub template repo (owner/name) qa_api_project(create=...)
    # fetches the project skeleton from. THE IDENTIFIER IS THE GATE: empty means
    # create= refuses BY NAME, so no second default-OFF boolean is added (a flag
    # nobody flips is dead code plus an untested off-path). Flag-policy category
    # (2) needs-config -- the same shape as qa_api_framework_path, and the
    # feature already sits behind default-OFF QA_API_TEST_ENABLED. The fetch is
    # unauthenticated: no token is read, sent or stored anywhere on this path.
    qa_api_template_repo: str = ""
    # Parent folder new API projects are created in. No default: an unset value
    # refuses rather than guessing where to write on the tester's disk.
    qa_api_projects_dir: str = ""
    # SQLite file holding the durable project registry (tools/api_project.py).
    # Resolved through tools/install_paths like qa_suite_store_path, so it does
    # not follow the client's working directory. NOT prep_store: a project must
    # outlive the 1h intake TTL (design review G1).
    qa_api_project_store_path: str = "data/api-projects.db"
    # B2 — the durable endpoint / auth-flow registry. A path, not a toggle:
    # the feature is already behind default-OFF QA_API_TEST_ENABLED, and
    # this only says WHERE the store lives (same shape as the line above).
    qa_api_registry_store_path: str = "data/api-registry.db"

    # AC anchoring (TICKET-7154 Fix 3): when the source ticket carries REAL
    # (source-parsed) acceptance criteria, drop generated cases that cite a
    # NON-EXISTENT AC id (hallucinated traceability). Default OFF — the advisory
    # "AC Anchoring" warning section is always shown; only the dropping is gated.
    qa_ac_anchoring_enforce: bool = False

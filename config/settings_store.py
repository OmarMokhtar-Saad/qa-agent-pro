"""TestRail, Xray, export, dist-mode and suite-store fields.

Plain ``BaseModel`` mixin; ``config.settings.Settings`` composes it.
"""

from __future__ import annotations

from pydantic import BaseModel


class StoreSettings(BaseModel):
    # TestRail API push (T-10). Base instance URL (e.g. https://acme.testrail.io),
    # a user email, and an API key. Dry run is UNCONDITIONAL since 2026-08-13
    # (flag-surface reduction, batch 6): TESTRAIL_DRY_RUN was DELETED, so a push
    # that passes no explicit dry_run argument always previews what WOULD be
    # created and never writes to the customer's TMS. push_suite(dry_run=False)
    # survives as a code-level override -- the decision moved out of .env, it
    # did not disappear. See docs/FEATURE_FLAGS.md.
    testrail_url: str = ""
    testrail_user: str = ""
    testrail_api_key: str = ""

    # Spec-document ingestion is GONE. QA_SPEC_INGEST_ENABLED and
    # QA_SPEC_RAG_PERSIST were deleted on 2026-08-13 (flag-surface
    # reduction, batch 8a) and the behaviour hardcoded OFF; on 2026-08-15
    # (dead-code deletion, batch D1) tools/doc_ingest.py itself was
    # deleted, and QA_MAX_SPEC_BYTES / QA_MAX_SPEC_CHARS went with it --
    # the module was their only real reader. The one surviving reference,
    # the spec_document prompt block in agents/test_scenario_agent.py,
    # carries the 20_000 char cap inline. A stale .env key is ignored.
    # See docs/RETIRED_CAPABILITIES.md.

    # Swagger/OpenAPI link ingestion -- UNCONDITIONAL since 2026-08-13
    # (flag-surface reduction, batch 6): QA_SWAGGER_ENABLED was DELETED and
    # the behaviour hardcoded ON. Read that as written: ON is the SHIPPED and
    # ADVERTISED value, NOT the code default this field carried. The
    # distribution .env template shipped QA_SWAGGER_ENABLED=true and the
    # qa-agent-pro README advertises a Swagger/OpenAPI link as a headline
    # input, so pinning the code default (False) would have silently deleted
    # a capability every real install was running. A pasted spec URL is
    # fetched with the same SSRF hardening as Jira/web content, condensed to
    # a bounded endpoint summary (tools/swagger_fetcher.py) and used to
    # ground API test-case generation; the only remaining gate is
    # tools.swagger_fetcher.looks_like_openapi_url(). See
    # docs/FEATURE_FLAGS.md.
    # The computed risk SCORE is exported into the XLSX Notes column whenever no
    # rule-pack note claims that cell. UNCONDITIONAL since 2026-08-12:
    # QA_XLSX_RISK_NOTES was DELETED (batch 4); the distribution .env had shipped
    # it `true` since 2026-07-30. tools/risk_scorer.py scores EVERY suite and the
    # sheet's row order IS the risk order (TC-001 = highest risk), but
    # risk_label / risk_score were never exported -- while the Notes column was
    # empty in 65/65 rows of the 2026-07-30 run. A rule-pack note ALWAYS wins, so
    # nothing is ever displaced. 2026-08-04: the risk LABEL left that cell -- it
    # duplicated the Priority column and contradicted it on 10/97 rows of that
    # day's run; only the SCORE, the sheet's row-order key, is written now.

    # Directory the auto-exported .xlsx is written to. A RELATIVE value is
    # resolved against the INSTALL ROOT by mcp_handlers._resolved_export_dir --
    # NOT against the process working directory, which is whatever the MCP
    # client happened to launch the server with, so one install printed a
    # different path per client (Claude Desktop / Code / Cursor) and a tester
    # could not reliably find their own file (2026-08-03). Defaults to the
    # gitignored data/exports so the file lands in a stable folder a
    # non-technical tester can find and re-open -- their own deliverable, never
    # auto-deleted. Set to "" for the legacy secure-temp behavior
    # (<tempdir>/qa_agents_exports/, 0600); an unusable value degrades to that
    # same temp path rather than failing the export. A plain string field: no
    # bool coercer, and it adds no internal import -- the resolution lives in
    # mcp_handlers for exactly that reason.
    qa_export_dir: str = "data/exports"

    # The Zephyr for Jira import export used to be described here. Its two flags
    # were deleted in batches 8a / 6 (2026-08-13) and the feature itself --
    # tools/zephyr_exporter.py, the two _zephyr_* seams in mcp_handlers, the
    # _auto_export_zephyr / _zephyr_pair_note / _suite_story_key chain and the
    # workbook + zfj_import_config.json pair -- was DELETED on 2026-08-15
    # (dead-code deletion batch D4). No field has existed here since 8a and none
    # is coming back; a stale QA_ZEPHYR_* line in an existing .env is ignored
    # (model_config uses extra="ignore"). See operations/runbook.md ->
    # "Zephyr for Jira import export -- DELETED 2026-08-15".

    # Distribution / test-cases-only mode. When ON, the UI exposes ONLY the
    # test-case generation flows (feature text / Jira / web URL / Swagger link
    # / mobile screens); bug-report, exploratory-coach and fine-tune
    # surfaces are hidden. Forced implicitly when those modules are absent
    # (the public distribution build ships without them).
    qa_dist_mode: bool = False

    # Xray (Jira test management) write-back -- mirrors the TestRail push above.
    # Xray Cloud client credentials + target project key. Dry run is
    # UNCONDITIONAL since 2026-08-13 (flag-surface reduction, batch 6):
    # XRAY_DRY_RUN was DELETED, so a push that passes no explicit dry_run
    # argument always previews what WOULD be created. push_suite(dry_run=False)
    # survives as a code-level override. xray_base_url is the fixed Cloud host
    # by default. See docs/FEATURE_FLAGS.md.
    xray_client_id: str = ""
    xray_client_secret: str = ""
    xray_project_key: str = ""
    xray_base_url: str = "https://xray.cloud.getxray.app"

    # SQLite file that persists generated suites so they survive a browser
    # refresh and stay re-exportable (T-01). Parent dirs are created on first
    # write; the store itself is never-raise (a corrupt DB degrades to
    # session-only, logged).
    qa_suite_store_path: str = "data/suites.db"

    # Host-mode pending-generation store (tools/prep_store.py). A prep record
    # (grounded prompt + checklist + category specs + bounds + provenance) is
    # persisted between qa_prepare_test_cases and qa_submit_suite. TTL after
    # which a prep record is expired on read, and the max serialized payload
    # size accepted (a host cannot wedge the store with a pathological
    # submission). Both never-raise-coerced as positive ints.
    qa_prep_ttl_s: int = 3600
    qa_prep_max_bytes: int = 4000000

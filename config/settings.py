"""Application settings, backed by pydantic-settings (QW-14 / I-045 / B-017).

House rule: this module imports **nothing internal**. All configuration comes
from the environment / ``.env`` only. Every field is validated with a lenient,
never-raising coercer so a malformed value (e.g. ``QA_RAG_TOP_K=abc``) degrades
to its documented default with a logged warning instead of crashing the whole
app at import time.

Field names map 1:1 to their upper-case environment variables (case-insensitive):
``qa_llm_backend`` <- ``QA_LLM_BACKEND``, ``jira_base_url`` <- ``JIRA_BASE_URL``,
and so on.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("qa_agents.settings")

# Load .env into os.environ so libraries that read the environment directly
# also see the values -- the anthropic SDK's ANTHROPIC_API_KEY is the live
# example. (This comment named langsmith / langgraph until 2026-08-15, when
# dead-code deletion Phase 2 batch P2-A deleted graph.py and dropped both
# packages; the call itself is unaffected.) BaseSettings additionally reads the
# same file for its own declared fields below.
load_dotenv()


_TRUTHY_TOKENS = ("1", "true", "yes", "on")
_FALSY_TOKENS = ("0", "false", "no", "off", "")

# Int fields that are nonsensical at <= 0 (timeouts, loop-bound counts,
# concurrency). A parseable but out-of-range value (e.g. QA_LLM_TIMEOUT_S=0/-5)
# is logged and replaced with the field default HERE, so downstream code
# (llm._int_setting) needs no silent second clamp.
# Deliberately EXCLUDED:
#   * qa_rag_recency_half_life_days / qa_rag_max_entries — 0 is a documented
#     "disable" / "unlimited" sentinel.
#   * the byte/char/image/comment CAPS (jira_max_comments, jira_max_images,
#     jira_max_image_bytes) — 0 there is a legitimate "allow none" cap;
#     bounding them would be a behaviour change out of scope for this
#     hygiene batch. qa_max_chat_images / qa_max_chat_image_bytes were named
#     here until 2026-08-30, when both fields were deleted with the last of
#     the backend settings.
#     qa_max_spec_bytes / qa_max_spec_chars were on this list until
#     2026-08-15, when batch D1 deleted them with tools/doc_ingest.py.
_POSITIVE_INT_FIELDS = frozenset(
    {
        "qa_checklist_max_items",
        "qa_checklist_max_prompt_chars",
        "qa_device_command_timeout",
        "qa_device_screenshot_timeout",
        "qa_update_timeout",
        "qa_host_dedup_max_groups",
        "qa_host_dedup_max_group_size",
    }
)


def _lenient_bool(value: object, field_name: str = "", default: bool = False) -> bool:
    """Parse a bool the same way the pre-pydantic settings did — never raises.

    An unparseable value resolves to ``default`` — the field's OWN declared
    default, passed in by ``_coerce_bool`` — not to a hard-coded False. That
    distinction is the whole point: every ``*_DRY_RUN`` flag defaults to True,
    so a hard-coded False fallback meant a blank or mistyped value silently
    DISARMED the guard it was meant to preserve (``TESTRAIL_DRY_RUN=`` in a
    .env opened a live external write). Two unparseable cases, both logged:

    - **Blank** (``FLAG=`` or whitespace): an env var that is present but empty
      is ambiguous config, not an explicit false, so it reads as unset and the
      field default wins. This is why "" is handled BEFORE _FALSY_TOKENS.
    - **Unrecognised** (a typo like ``treu``): same fallback, louder message.

    Detection, not prevention — the never-raise contract is unchanged.
    """
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in _TRUTHY_TOKENS:
        return True
    if token == "":
        logger.warning(
            "Blank boolean %s=%r — a present-but-empty value reads as unset, so "
            "the field default %r applies; write an explicit %s/%s to override",
            (field_name or "setting").upper(),
            value,
            default,
            _TRUTHY_TOKENS[1],
            _FALSY_TOKENS[1],
        )
        return default
    if token in _FALSY_TOKENS:
        return False
    logger.warning(
        "Invalid boolean %s=%r — expected one of %s (true) / %s (false); "
        "falling back to the field default %r",
        (field_name or "setting").upper(),
        value,
        "/".join(_TRUTHY_TOKENS),
        "/".join(t for t in _FALSY_TOKENS if t),
        default,
    )
    return default


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- The backend settings -- ALL DELETED 2026-08-16 (P2-I) --------------
    # `QA_LLM_BACKEND`, `QA_LLM_MODEL`, `ANTHROPIC_API_KEY`, `CURSOR_API_KEY`,
    # `QA_LLM_TIMEOUT_S`, `QA_CATEGORY_STALL_S` / `_STRIKES`, `QA_CURSOR_MODEL`,
    # `QA_CURSOR_FALLBACK_MODEL`, `QA_LLM_STRICT_HOST` and the two
    # `QA_PROMPT_CACHE_*` knobs all lost their ONLY reader when dead-code
    # deletion P2-G deleted `llm.py`'s four public coroutines and all three
    # backends (`cli` / `api` / `cursor`) on 2026-08-16. An unread setting is
    # the stale-flag class `tests/test_no_deleted_flag_in_output.py` exists to
    # catch, so the fields go rather than sit as inert identifiers.
    #
    # A stale line for any of them in an existing `.env` is IGNORED, not an
    # error: `model_config` uses `extra="ignore"`. That is the same contract
    # P2-G1 gave `QA_CHECKLIST_MATCH_LOW` / `_MAX_PAIRS`, and it is pinned in
    # `tests/test_settings.py`.
    #
    # 2026-08-30 (audit finding 8 follow-up): `qa_classifier_model` was the
    # THIRTEENTH field of that P2-I set, held back at the time because
    # `tools/mcp_handlers.py` still read it twice to label which model the
    # ambiguity gate would name. Both reads went with the server-side ambiguity
    # gate in P2-J (2026-08-16), so it named a model nothing could call. It is
    # DELETED here together with `qa_max_chat_images` / `qa_max_chat_image_bytes`
    # below, on the same reasoning P2-I recorded: a settings name that promises
    # a model choice this server cannot act on is the stale-flag class
    # `tests/test_no_deleted_flag_in_output.py` exists to catch. No backend
    # remains anywhere in this tree -- every generative step runs in the
    # tester's own chat model -- so there is no model, key, timeout or model
    # choice left for `.env` to offer. Restoring a server-side call would be a
    # NEW implementation and an architectural decision (CLAUDE.md's
    # host-boomerang house rule), bringing its own settings with it.

    # ---- Structured JSON via forced tool use (api backend only) -------------
    # UNCONDITIONAL since 2026-08-13 (flag-surface reduction, batch 8a):
    # QA_STRUCTURED_JSON_ENABLED and QA_STRUCTURED_JSON_STRICT were DELETED as
    # settings and the behaviour hardcoded ON. On the `api` backend llm.ask_json
    # no longer asks the model for JSON in prose: it compiles the pydantic
    # response_model's schema into an Anthropic TOOL input_schema, forces that
    # one tool with tool_choice, and sends "strict": true (constrained decoding)
    # wherever the model supports it. A JSONDecodeError is then structurally
    # impossible on that branch -- which is the point, because a single parse
    # failure re-runs an ENTIRE test-case category. Nothing about the degradation
    # ladder changed: llm.py still sanitises the schema, still memoises an API
    # rejection per (model, schema), and still falls back to non-strict forced
    # tool use and then to the JSON-in-prompt path. The cli/cursor backends drive
    # a subprocess with no tool API and are byte-for-byte unchanged.
    # The surviving seams are llm._structured_json_enabled() /
    # llm._strict_json_enabled(); both read NO setting.
    # See docs/FEATURE_FLAGS.md.

    # Still read: used ONLY to recognise a self-hosted Jira on a custom domain
    # (tickets.example.com) as a ticket URL rather than a generic web page.
    jira_base_url: str = ""
    # JIRA_EMAIL / JIRA_API_TOKEN were DELETED on 2026-08-15 (dead-code
    # deletion, batch D1) together with tools/jira_attachments.py, which
    # was their ONLY reader in the whole tree. Nothing has sent this pair
    # anywhere since 2026-08-13, when the credentialed attachment fetch
    # was hardcoded OFF. An existing .env still carrying the keys loads
    # cleanly and silently: model_config uses extra="ignore".
    #
    # Re-adding them is part of reviving that fetch, and the safety
    # contract a revival must satisfy (per-hop host re-check, HTTPS-only
    # Basic auth to the configured Jira host, MIME allowlist, size caps,
    # never-raise) is in docs/RETIRED_CAPABILITIES.md.
    # Tool-name prefix the CALLING agent uses for its Atlassian MCP tools.
    # Claude Code / Desktop expose them as `mcp__atlassian__getJiraIssue`;
    # other clients namespace differently, so the directive this server returns
    # must be adjustable rather than hardcoded. Empty falls back to the Claude
    # form. Not a feature flag -- it changes wording, never behaviour.
    qa_jira_mcp_tool_prefix: str = "mcp__atlassian__"
    # Jira custom-field id that holds Acceptance Criteria. Defaults to the common
    # Jira Software default; different instances use different ids, so make it
    # configurable (QW-11 / I-023 / B-015). When empty on a ticket, jira_fetcher
    # falls back to scanning the description for an "Acceptance Criteria" heading.
    jira_ac_field: str = "customfield_10016"

    # Searching the OTHER custom fields for one whose value reads like
    # requirements when the configured `jira_ac_field` does not is OFF, and
    # unsettable: QA_JIRA_AC_FIELD_DISCOVERY was DELETED 2026-08-13
    # (flag-surface reduction, batch 8a) and hardcoded to its default, False.
    # Adopting the wrong field is exactly the failure that made a date field's
    # timestamp the only "acceptance criterion" on a real run, and the search was
    # never validated against a real workspace. `qa-doctor` still discloses which
    # field was resolved, so a mis-configured field stays visible without this.
    # The surviving seam is tools.jira_mcp._ac_field_discovery_on().
    # See docs/FEATURE_FLAGS.md.

    # Asking the tester's own chat model to classify every generated case as
    # entailed / ungrounded / unspecified against the ticket is OFF, and
    # unsettable: QA_HOST_GROUNDING_REVIEW_ENABLED was DELETED 2026-08-13
    # (flag-surface reduction, batch 8a) and hardcoded to its default, False.
    # It changed what the host was ASKED to do and spent the tester's tokens on
    # one judgement per case for a precision nobody had measured, which is what
    # made it an experiment rather than an advisory. tools/grounding_verdicts.py
    # and agents.host_mode.build_grounding_section are RETAINED with every bound
    # they carry (ids matched against the suite's own, verdicts enum-gated, notes
    # capped, a 40% proportional ceiling, cases MOVED rather than deleted), so a
    # submission that carries verdicts anyway is still handled safely. What is
    # gone is only the INSTRUCTION that asks for them; the seam is
    # agents.host_mode.grounding_review_enabled(). See docs/FEATURE_FLAGS.md.
    # Parent-story context (JIRA_FETCH_PARENT). Default **ON** — a deliberate
    # exception to the constitution's defaults-OFF rule. The two flags this
    # comment used to name alongside it are both gone: QA_AUTO_EXPORT_XLSX was
    # hardcoded ON in batch 4 (2026-08-12) and QA_AMBIGUITY_GATE_SEVERITY was
    # deleted by P2-J2 with the gate it switched.
    # A Jira SUB-TASK ("Add Apple Pay button") carries almost no requirements
    # — they live on the parent story — so fetching only the sub-task makes
    # the generator either fabricate a suite or trip the ambiguity gate, which
    # is exactly the failure this flag exists to prevent. parent/subtasks/
    # issuelinks are already in the DEFAULT REST field set (free to extract);
    # only the parent BODY costs one extra authenticated GET, and a ticket
    # with no parent makes no extra call at all. The result is injected as
    # clearly-labelled BACKGROUND, never as the thing under test.
    # Set JIRA_FETCH_PARENT=false to disable (complete kill-switch).
    jira_fetch_parent: bool = True
    # Character cap on the composed parent/related-issue BACKGROUND block so a
    # huge epic description can never crowd out the sub-task under test. 0
    # means "emit no background block" (same convention as jira_max_comments),
    # which is why this field is deliberately NOT in _POSITIVE_INT_FIELDS.
    jira_max_parent_chars: int = 2500

    # 2026-08-03 (user-approved) -- the sibling USER STORIES under the same
    # parent, WITH their bodies. _extract_subtasks / _extract_issuelinks only
    # ever see {key, summary, status} from arrays that ride along with the issue
    # fetch, so the requirements WRITTEN IN a sibling story were invisible: a
    # sub-task inherits them and a tester reading the board sees them. ON by
    # default for the same reason as jira_fetch_parent (a default-OFF switch
    # leaves the generator guessing), and a deliberate exception to the
    # defaults-OFF rule, documented in docs/FEATURE_FLAGS.md. Costs ONE extra
    # host-side searchJiraIssuesUsingJql call, and only when a parent exists.
    jira_fetch_sibling_stories: bool = True
    # How many sibling stories may contribute a BODY. Deliberately its own knob
    # rather than reusing the 10-issue _MAX_RELATED_ISSUES list cap: listing ten
    # keys costs a line each, but ten BODIES split the character budget ten ways.
    # A live SHYJ-5645 run made the difference concrete -- 3000/10 = 300 chars per
    # story cut every markdown table mid-row and grounded nothing, while the host
    # had to ship 83k characters so 3k could be kept. Five stories at ~600 chars
    # each carry a readable use-case instead. 0 disables the block.
    jira_max_sibling_stories: int = 5
    # Total character budget for the sibling block, ON TOP of
    # jira_max_parent_chars (the composed background block is capped at the sum,
    # so sibling prose can never displace the parent's own description). 0 means
    # "emit no sibling block", which is why this is deliberately NOT in
    # _POSITIVE_INT_FIELDS -- same convention as jira_max_parent_chars.
    jira_max_sibling_chars: int = 3000

    # Ticket comments. 2026-08-03 (user-approved): ON by default. The extra
    # REST call (/issue/{key}/comment) that justified defaulting OFF is GONE --
    # Jira is read through the calling agent's own Atlassian MCP connection,
    # where `comment` is one more entry in the `fields` list of the SAME
    # getJiraIssue call, so there is no second network call to avoid any more.
    # A Jira description is a snapshot taken at refinement while the CURRENT
    # requirements accumulate in the thread, which is exactly what a tester
    # needs. Deliberate exception to the defaults-OFF rule (see
    # docs/FEATURE_FLAGS.md); JIRA_FETCH_COMMENTS=false is the kill-switch.
    jira_fetch_comments: bool = True
    jira_max_comments: int = 5

    # --- Comment reconciliation (Batch 1) — DELETED 2026-08-15. -----------
    # Batch 8b-ii (2026-08-14) deleted QA_COMMENT_RECONCILE_ENABLED and
    # hardcoded the pipeline OFF -- this field's own code default AND the
    # value .env.example shipped, so no install changed -- and kept the six
    # qa_comment_reconcile_* knobs as revival tuning behind the named seam
    # tools/comment_reconciler.enabled(). Dead-code deletion batch D5
    # (2026-08-15) deleted the module that seam guarded, so all six lost their
    # last reader and went with it: _MAX_COMMENTS, _BOT_AUTHORS,
    # _FIELD_THRESHOLD, _DEDUP_THRESHOLD, _MAX_AMENDMENTS, _MAX_CHARS. They
    # also left BOTH validator lists above (_POSITIVE_INT_FIELDS and the
    # second field_validator, which hold different fields in a different
    # order) and the _coerce_reconcile_threshold list.
    #
    # A stale QA_COMMENT_RECONCILE_* line in an existing .env still loads --
    # model_config uses extra="ignore" -- and now does nothing at all.
    # JIRA_MAX_COMMENTS (above, default 5) is the ONLY comment bound left, and
    # tools/jira_mcp leaves the raw "## Comments" dump in the ticket text.
    # The revival contract, including the containment control that sanitised
    # attacker-writable comment text before it reached a model, is
    # docs/RETIRED_CAPABILITIES.md section 4.

    # Image attachments. 2026-08-03 (user-approved): ON by default -- but read
    # what it does and does NOT buy. On the MCP Jira path this yields attachment
    # METADATA ONLY ({filename, mime, size}): tools/jira_mcp.py makes no
    # outbound HTTP request by hard rule, the Atlassian MCP server returns no
    # attachment bytes, and the byte-fetching REST path
    # (jira_fetcher._fetch_jira_images) is ANTHROPIC_API_KEY-gated. So ON buys
    # two real things and NOT vision: the filenames themselves become grounding
    # ("the ticket has error_state.png"), and images_unavailable fires the
    # existing handler notice that NAMES them and asks the tester to attach the
    # screenshots to the chat -- where they ARE analysed, via IMAGE_JOB. That
    # notice is the point: OFF meant silently generating a suite that never saw
    # the screenshots and never saying so.
    jira_fetch_images: bool = True
    jira_max_images: int = 3
    jira_max_image_bytes: int = 5_000_000  # Anthropic's own per-image vision cap

    # --- Jira attachment BYTE retrieval -- REMOVED 2026-08-13. --------------
    # QA_JIRA_ATTACHMENT_FETCH_ENABLED was DELETED and the behaviour hardcoded
    # OFF (flag-surface reduction, batch 6): a kill-switch is no longer a
    # tester-editable .env value, because .env holds credentials and paths and
    # not behaviour. tools/jira_attachments.enabled() returns the False constant,
    # so nothing in this tree makes the credentialed
    # /rest/api/3/attachment/content/{id} request and a ticket's screenshots
    # reach a model only when a tester attaches them to the chat. See
    # docs/FEATURE_FLAGS.md.

    # Direct chat image uploads: `QA_MAX_CHAT_IMAGES` / `QA_MAX_CHAT_IMAGE_BYTES`
    # were DELETED on 2026-08-30 with `QA_CLASSIFIER_MODEL` above. They capped
    # what `tools/image_description.py` would read from an upload before handing
    # it to `llm.ask_vision()` on the `api` backend; that call site migrated to
    # the host on 2026-08-15 and the coroutine and every backend went in P2-G on
    # 2026-08-16, leaving both fields with no reader at all. Attached screenshots
    # now ride to the tester's OWN chat model as MCP image content, whose limits
    # are that client's, not this server's -- a cap here could not have been
    # enforced and telling an operator otherwise was the false part.
    # Mobile device capture -> test cases -- UNCONDITIONAL since 2026-08-13
    # (flag-surface reduction, batch 7 (needs-config)): QA_MOBILE_CAPTURE was
    # DELETED and the behaviour hardcoded to the value the DISTRIBUTION ships
    # (`true`), NOT this field's code default (`False`) -- the same divergence,
    # and the same reasoning, batch 6 recorded for QA_SWAGGER_ENABLED. Testers
    # can always list attached Android/iOS devices, pick one, and capture
    # screens; tools/mcp_handlers._mobile_capture() returns the True constant.
    # qa_capture_screens makes NO server-side vision call (the screens ride to
    # the tester's own chat model as MCP image content), so that path needs no
    # credential. CORRECTED 2026-08-30 (audit finding 8): this comment used to
    # add that "the Feature-Analysis mobile modes still call llm.ask_vision(),
    # which needs ANTHROPIC_API_KEY regardless of QA_LLM_BACKEND". All three
    # names are gone -- `image_description.describe_images` migrated to the
    # host on 2026-08-15, and P2-G deleted `ask_vision` and both settings on
    # 2026-08-16. NO mode of Feature Analysis reaches a server-side model, and
    # no key is needed for any of this. Device discovery/capture is bounded by
    # the two timeouts below. See docs/FEATURE_FLAGS.md.

    # Timeout (seconds) for device-discovery commands (adb devices / simctl list).
    qa_device_command_timeout: int = 20
    # Timeout (seconds) for a single screenshot capture (larger -- image transfer).
    qa_device_screenshot_timeout: int = 60

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
    # flag deleted" outcome for an experiment. The seams are
    # mcp_server._feature_analysis_enabled and
    # tools.mcp_handlers._feature_analysis_enabled. The test-cases-only
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

    # AC anchoring (SHYJ-7154 Fix 3): when the source ticket carries REAL
    # (source-parsed) acceptance criteria, drop generated cases that cite a
    # NON-EXISTENT AC id (hallucinated traceability). Default OFF — the advisory
    # "AC Anchoring" warning section is always shown; only the dropping is gated.
    qa_ac_anchoring_enforce: bool = False

    # QA_TEST_PLAN_ARTIFACTS was DELETED on 2026-08-14 (flag-surface
    # reduction, batch 8b-ii) and the behaviour hardcoded OFF -- this field's
    # own code default, and it appeared in no shipped template, so no install
    # changed. ON it built an AC-Validation report (only when the ticket
    # carried REAL source acceptance criteria) and a Test Plan / Strategy
    # section -- at most two extra ask_json calls -- rendered into the summary
    # and added as extra XLSX sheets. The two builders and the
    # test_plan_artifacts_enabled() seam were deleted on 2026-08-16 (P2-F3) and
    # the host-side TEST_PLAN_JOB that replaced them the same day (P2-H), so
    # there is no seam left to flip and no artifacts are produced at all.
    # tools/test_plan_report's render helpers and the two XLSX sheets are
    # RETAINED (unreachable, but removing them is a product decision).

    # QA_LLM_RISK_SCORING was DELETED on 2026-08-14 (flag-surface reduction,
    # batch 8b-ii) and the behaviour hardcoded OFF -- this field's own code
    # default, and it appeared in no shipped template, so no install changed.
    # ON it replaced the deterministic priority x type heuristic with ONE
    # batched ask_json call judging each case's business risk, falling through
    # to the heuristic on any failure. score_with_llm and its seam were deleted
    # on 2026-08-16 (P2-F3), and apply_host_risk -- the chat-only overlay --
    # the same day with the RISK_JOB cluster (P2-H). The heuristic is now the
    # only thing that scores a case, and reviving either half is a fresh
    # implementation. See docs/FEATURE_FLAGS.md.

    # Test-data strategy: the per-category generation prompt asks the model to
    # populate each case's ``test_data`` plan (which fields need unique-per-run /
    # seed-account / chained / static values, each with a SAFE fake example and no
    # real-looking PII), and the exporters + chat summary render a Test Data
    # column/note. UNCONDITIONAL since 2026-08-12: QA_TEST_DATA_STRATEGY was
    # DELETED (flag-surface reduction, batch 4) and the behaviour hardcoded ON.
    # The distribution .env had shipped it `true` since 2026-07-30.

    # The "Edge Cases" fan-out category is typed Functional, not Exploratory.
    # UNCONDITIONAL since 2026-08-12: QA_EDGE_CASES_FUNCTIONAL_TYPE was DELETED
    # (batch 4); the distribution .env had shipped it `true` since 2026-07-30.
    # CATEGORIES[3] used to tell the model the preferred `type` for that category
    # is "Exploratory" while the cases it produces are fully SCRIPTED, so the
    # 2026-07-30 run exported 8 scripted cases as Exploratory and the XLSX
    # Summary reported "Exploratory 8 / Performance 0" -- test-type metrics
    # describing unscripted charter testing for a suite that contains none.
    # This is a PROMPT change, so tests/fixtures/server_mode_equivalence/ (which
    # records the 8 category prompts VERBATIM) was RE-CAPTURED with it applied.

    # A QUALIFIER-PREFIXED module label is merged onto the bare label it
    # qualifies. UNCONDITIONAL since 2026-08-12:
    # QA_MODULE_PREFIX_NORMALIZE_ENABLED was DELETED (batch 4); the distribution
    # had shipped it `true` since v1.39.3 and tools/env_heal repaired installs
    # still carrying the superseded shipped `false`.
    # tools/quality_checks.normalize_module_names buckets on a CASEFOLDED key, so
    # "Sehhaty Store - Cancel Order" and "Cancel Order" were different keys and
    # never merged: a real 2026-08-03 run shipped ONE feature split 12 / 86
    # across exactly those two labels, fragmenting every group-by-module view
    # (Jira, TestRail, the XLSX pivot).
    # THE SAFETY RULE IS UNCHANGED and is what makes this safe to hardcode:
    # tools/quality_checks._qualifier_prefix_merges merges ONLY on TAIL
    # containment, REFUSES head containment ("Store Wallet - Top Up" is a
    # SUB-module of "Store Wallet", not a variant of it) and refuses a tail
    # claimed by rival qualifier families ("Admin - Login" + "User - Login").

    # Acceptance criteria are extracted from a USE-CASE TABLE description when the
    # ticket carries no "Acceptance Criteria" heading. UNCONDITIONAL since
    # 2026-08-12: QA_JIRA_UC_TABLE_AC_ENABLED was DELETED (batch 4). It had
    # already been default **ON** since 2026-08-03, so this is a flag COLLAPSE,
    # not a behaviour change.
    #
    # The AC fallback in tools/jira_mcp only understands an "Acceptance Criteria"
    # heading followed by a block. A whole ticket family writes its requirements
    # as a markdown UC table instead -- rows labelled Basic Flow / Alternative
    # Flow / Business Rules / Post-condition -- with no such heading anywhere.
    #
    # WHY THE DEFAULT FLIPPED (kept verbatim as history: it records a real
    # incident). This shipped OFF on the reasoning that it adds ticket text to
    # the generation prompt, and a mis-parse could introduce requirements the
    # ticket never stated. That weighed the risk against the wrong baseline. With
    # it OFF the run does not get NO acceptance criteria -- the host's AC_JOB
    # SYNTHESIZES them, and the first production v1.34.0 run finalized with SIX
    # model-invented criteria and a "6/6 traced, all covered" RTM built on them.
    # Measured on that same ticket, ON yields FOUR criteria read out of the
    # ticket's own table. So the real choice is not "extra text vs no extra
    # text", it is "criteria read from the ticket vs criteria invented by a
    # model", and reading them is plainly safer.
    #
    # The mis-parse risk stays bounded and disclosed rather than hidden: the
    # extractor takes only requirement-bearing rows (context rows like
    # Description / Actor / Pre-condition are skipped), caps at 12 rows x 600
    # chars, an explicit "Acceptance Criteria" heading still wins, and anything
    # it produces still flows through the untrusted-text path.

    # Startup re-registration of this server in editor MCP configs -- REMOVED
    # 2026-08-13 (flag-surface reduction, batch 6): QA_AUTO_REGISTER_CLIENTS was
    # DELETED and hardcoded OFF, so the dist launcher's startup pass no longer
    # exists and nothing writes to files outside the install dir unattended.
    # Running connect.sh by hand remains the answer for an editor installed
    # after qa-agent-pro, and qa-doctor points at it. See docs/FEATURE_FLAGS.md.

    # Write the hosted Atlassian MCP entry (Jira Cloud, OAuth) into the clients
    # that keep it in a FILE -- Cursor today -- when the tester runs connect.sh or
    # connect.ps1. Until 2026-08-04 nothing in this tree could write that entry at
    # all: tools/client_registry hardcoded the stdio shape, so every tester was
    # told to hand-edit mcpServers JSON, and that is the step they fail at.
    #
    # ON by default, which is a deliberate exception to "new features default
    # OFF", justified narrowly:
    #
    # * it runs ONLY from a script the tester invoked by hand -- never from the
    #   launcher's startup pass, which still registers this server and nothing
    #   else (QA_AUTO_REGISTER_CLIENTS governs that, separately and still OFF);
    # * the write is insert-only, atomic, locked and backed up, and never rewrites
    #   an existing `atlassian` entry (client_registry.register_atlassian);
    # * the failure mode of OFF is worse than the failure mode of ON: OFF means a
    #   non-technical tester edits JSON by hand, ON means one extra key in a file
    #   they can delete.
    #
    # It authorizes NOTHING: OAuth still needs one click in the editor, and
    # Claude Desktop's hosted Connector has no file to write at all.
    #
    # 2026-08-04, same day: this ALSO governs qa-doctor, which now writes the entry
    # itself when it is missing. Not scope creep -- v1.42.0 could not reach the
    # installs that needed it. connect.sh/.ps1 run only from the installer or by
    # hand, the startup pass above registers THIS server and nothing else, and the
    # updater never calls connect, so an install that auto-updated into v1.42.0 got
    # the code and none of the behaviour. A Windows machine on 1.42.0 still read
    # "Not connected -- add this to ~/.cursor/mcp.json". Only fresh installs were
    # fixed, which is the opposite of where the users are.
    #
    # UNCONDITIONAL since 2026-08-13 (flag-surface reduction, batch 6):
    # QA_REGISTER_ATLASSIAN_MCP was DELETED and hardcoded ON, which is the value
    # both the code default and the shipped dist .env already carried, so no
    # install changes behaviour. The rationale above is kept because it is the
    # justification for the write, not for the flag; the connect scripts and
    # qa-doctor now always write the entry, insert-only, atomic, locked and
    # backed up. The unattended startup pass it was contrasted with
    # (QA_AUTO_REGISTER_CLIENTS) was deleted in the same batch and hardcoded
    # OFF, so the INVOKED-vs-SILENT line now separates code paths rather than
    # two flags. See docs/FEATURE_FLAGS.md.

    # Surgical quality retry -- hardcoded ON 2026-08-12 (flag-surface
    # reduction, batch 3): QA_QUALITY_REMINDER_UPFRONT and
    # QA_SURGICAL_QUALITY_RETRY were DELETED. See docs/FEATURE_FLAGS.md and
    # .claude/plans/plan-surgical-retry.md.
    #
    # The per-category quality gate (tools/quality_checks.quality_ratio) flags a
    # category whose steps are >30% vague/placeholder. The stricter reminder is
    # now folded into EVERY category's FIRST prompt, and when the gate still
    # trips the repair is SURGICAL -- only the flagged cases are re-asked (a
    # smaller ask_json call carrying just those cases + targeted feedback,
    # merged back by stable_id). The legacy full-category retry it replaced is
    # gone; there is no setting left that can bring either behaviour back.

    # RAG corpus grounding -- UNCONDITIONAL since 2026-08-13 (flag-surface
    # reduction, batch 7 (needs-config)): QA_RAG_ENABLED was DELETED and the
    # behaviour hardcoded to the value the DISTRIBUTION ships (`true`), NOT this
    # field's code default (`False`) -- the QA_SWAGGER_ENABLED divergence again.
    # Retrieval is always attempted, and an EMPTY corpus is already a no-op
    # (query_corpus returns no hits and never raises), which is what made the
    # per-install switch redundant. The knobs below -- storage path, threshold,
    # top-k, similarity mode, recency, entry cap and the relevance floor -- are
    # the surviving controls. See docs/FEATURE_FLAGS.md.
    qa_rag_storage_path: str = "corpus"
    qa_rag_similarity_threshold: float = 0.3
    qa_rag_top_k: int = 5
    # Corpus similarity metric: "jaccard" (default, set overlap), "cosine"
    # (TF-IDF cosine — weights rare/discriminative terms) or "bm25" (Okapi
    # BM25, the consensus lexical-retrieval baseline; saturation-normalized
    # to [0,1) so the threshold above applies unchanged). (I-051)
    qa_rag_similarity_mode: str = "jaccard"
    # Freshness boost: entries decay with this half-life (days) and fresh ones
    # get up to +15% score. 0 disables (default — no behavior change).
    qa_rag_recency_half_life_days: int = 0
    # Corpus size cap per file: adding beyond it prunes the oldest entries.
    # 0 = unlimited (default).
    qa_rag_max_entries: int = 0

    # Relevance FLOOR for the injected "## Similar Past Test Cases" block.
    # 0.0 = OFF (default = today's behaviour: _enrich_with_rag injects ALL top-k
    # hits with no floor -- only the Duplicate-Risk block was ever thresholded,
    # by qa_rag_similarity_threshold). On the 2026-07-30 run that put 5 snippets
    # from unrelated past tickets into the prompt whose TOP score was 0.0875
    # (886-entry corpus, bm25): wasted host-context tokens and real topic-bleed
    # risk into the tester's own model. The scale is MODE-DEPENDENT (jaccard /
    # cosine / bm25 normalise differently), so a value belongs with a pinned
    # QA_RAG_SIMILARITY_MODE. Suppression is always logged with counts -- silence
    # here would be indistinguishable from an empty corpus or a broken query. The
    # Duplicate-Risk block keeps its own threshold and is UNAFFECTED.
    # Coerced by _coerce_checklist_float, which CLAMPS to [0, 1]: an operator
    # writing 15 (meaning "15%") would otherwise suppress the block permanently.
    qa_rag_similar_min_score: float = 0.0

    # --- Semantic embeddings (opt-in; default disabled) --------------------
    # Optional embedding backend powering semantic dedup + vector RAG ranking.
    #   ""       : disabled (default) — zero cost, no optional import.
    #   "local"  : sentence-transformers (extra: pip install -e ".[embeddings]").
    #   "voyage" : Voyage AI over httpx (needs VOYAGE_API_KEY; no new hard dep).
    # tools/embeddings.py degrades gracefully (never-raise) when the backend or
    # its dependency/key is missing.
    qa_embeddings_backend: str = ""
    # Model id override. Empty uses the backend default (local ->
    # all-MiniLM-L6-v2, voyage -> voyage-3).
    qa_embeddings_model: str = ""
    # Voyage API key (voyage backend). .env only; falls back to $VOYAGE_API_KEY.
    voyage_api_key: str = ""
    # Cosine threshold at/above which two cases are treated as the same for
    # intra-suite semantic dedup. Only used when qa_embeddings_backend is set.
    qa_semantic_dedup_threshold: float = 0.9
    # Intra-suite semantic dedup -- REMOVED as a setting 2026-08-13
    # (flag-surface reduction, batch 7 (needs-config)):
    # QA_SEMANTIC_DEDUP_ENABLED was DELETED and hardcoded OFF, so
    # agents.test_scenario_agent.semantic_dedup_enabled() returns the False
    # constant and the generation pipeline never merges cases on embedding
    # similarity. QA_EMBEDDINGS_BACKEND survives and still powers vector RAG
    # ranking -- which is exactly the separation this gate existed to protect,
    # now enforced in code rather than by a second .env line. OFF is also the
    # SAFE direction: this was the one gate in the batch whose ON state DROPS
    # generated cases. The threshold above is retained with the
    # retained-for-revival _semantic_dedupe_cases path.

    # --- Atomic Requirements Checklist (Batch 2) --------------------------
    # QA_ATOMIC_CHECKLIST_ENABLED was DELETED on 2026-08-14 (flag-surface
    # reduction, batch 8b-ii) and the behaviour hardcoded **ON**. Unlike every
    # other flag in batches 8b-i/8b-ii this DOES change shipped behaviour:
    # the field defaulted False, .env.example shipped `false` and the dist
    # template never carried the key, so the checklist was OFF on every
    # install. It is now unconditional -- the flag policy's "promoted to
    # default behaviour (flag deleted)" exit for an experiment. The pipeline:
    #   Pass 1  tools/atomic_checklist.decompose_to_checklist -> an unbounded,
    #           EARS-shaped, source-tagged checklist of every independently-
    #           verifiable outcome. On the (unconditional) host route this is
    #           CHECKLIST_JOB / step 0d, so it costs this server NO LLM call.
    #   Pass 2  the 8-category fan-out with that checklist injected as its OWN
    #           untrusted, CLUSTERED block (constraint-decay mitigation).
    #   Pass 3  tools/rtm.match_checklist -> a DETERMINISTIC EXTERNAL matcher
    #           that recomputes coverage instead of trusting the generating
    #           model's self-assigned requirement_id. Falls back to the
    #           lexical tier when no embeddings backend is configured, so a
    #           bare install degrades rather than fails.
    # The named seam is tools/atomic_checklist.checklist_enabled(), a True
    # constant, and tests/conftest.py pins it False suite-wide -- unpinned,
    # every generation test would decompose and make a real ask_json call.
    #
    # QA_CHECKLIST_NLI_ENABLED (tier b: ONE batched entailment judgement over
    # the ambiguous similarity band) and QA_CHECKLIST_ADJUDICATE_ENABLED
    # (tier c: a final adjudication over ONLY the pairs tier b left "unsure")
    # were DELETED the same day and hardcoded OFF -- their own code defaults
    # and the value .env.example shipped. The ambiguous band is reported as
    # uncovered, which is what every install already did. Both are retained
    # behind tools/rtm._nli_tier_enabled() / _adjudicate_tier_enabled(), and
    # tools/rtm's degradation note still fires under a revived seam so a
    # revival cannot silently ship an undisclosed degraded measurement.
    # See docs/FEATURE_FLAGS.md.
    # QA_CHECKLIST_REMEDIATION_ENABLED was DELETED on 2026-08-14
    # (flag-surface reduction, batch 8b) and the behaviour hardcoded OFF --
    # this field's own code default, and the value every shipped template
    # carried. ON it made the bounded critic loop's stop condition "every
    # checklist item is traced" instead of "the LLM critic ran out of
    # patience"; its registry rationale said plainly that it needs a tally
    # proven reliable first, and that measurement was never made. OFF the
    # legacy critic remains the stop condition, which is the state the whole
    # suite is written against. NOTHING is deleted: the checklist-driven
    # branch is retained and
    # agents.test_scenario_agent.checklist_remediation_enabled() is the named
    # seam -- a revival is one line there. See docs/FEATURE_FLAGS.md.
    # Embedding-cosine bands. score >= high -> HIGH-confidence match;
    # low <= score < high -> the ambiguous band, REPORTED AS UNCOVERED since
    # 2026-08-14 (tiers (b)/(c) are False seams, so nothing is handed to them);
    # score < low -> no match. Thresholds are dataset-dependent (TraceLLM tunes
    # 0.01..1.0 per domain against labelled ground truth); these are
    # conservative project-level defaults, NOT tuned optima. The lexical TF-IDF
    # fallback uses its own fixed constants in tools/rtm.py because its scores
    # live on a different scale.
    qa_checklist_match_high: float = 0.75
    # Phase-0 granularity gate. Below this the decomposition is reported as
    # probably inflated / under-split -- ADVISORY only, it never blocks
    # generation (house rule: log and degrade).
    qa_checklist_min_granularity: float = 0.6
    # Hard caps: decomposed items (anti-inflation) and the injected prompt
    # block. MAX_PROMPT_CHARS is DERIVED FROM MAX_ITEMS: a rendered line
    # ("- CL-017 [event_driven] When the user taps cancel, the system shall
    # redirect the user to the Appointment Card screen.") is ~120 chars, so
    # 200 items need ~24,000; 32,000 leaves headroom. A smaller cap would make
    # the tool truncate its
    # OWN prompt and then score the truncated items as coverage gaps; that is
    # now impossible (format_checklist_prompt_block reports exactly which ids
    # it presented and the matcher excludes the rest), but the default must
    # still fit a full checklist so the NOT-PRESENTED bucket stays empty in
    # normal operation.
    qa_checklist_max_items: int = 200
    qa_checklist_max_prompt_chars: int = 32000

    # --- Batch 3 rule packs (REMOVED as settings) --------------------------
    # QA_BILINGUAL_RULES, QA_ATOMICITY_RULES and QA_STANDING_RULES were
    # DELETED on 2026-08-14 (flag-surface reduction, batch 8b) and all three
    # hardcoded OFF -- each field's own code default, and the value every
    # shipped template carried, so no install changes behaviour. All three
    # were unvalidated experiments held at OFF past the point the flag policy
    # allows: EN/AR pair extraction was never checked against a real bilingual
    # corpus, the anti-bundling detectors' false-positive rate was never
    # measured, and the standing rules' two-hit circumstantial API trigger was
    # never tuned.
    #
    # NOTHING is deleted. tools/rule_packs.bilingual_rules_enabled(),
    # .atomicity_rules_enabled() and .standing_rules_enabled() are the named
    # seams and each revival is one line there. With all three off
    # RulePackResult.active is False and build_rule_packs returns an inert
    # result -- a state tools/rule_packs' own module docstring already
    # documents as supported, because it is the same state the packs were
    # already in on every install. They were always PURE + SYNCHRONOUS: zero
    # LLM calls and zero network, on or off, so this removes no cost either.
    # See docs/FEATURE_FLAGS.md.

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

    # --- Refuse a host submit with no verified ambiguity preflight (ON) -----
    # QA_HOST_AMBIGUITY_REVIEW_ENABLED hands the SHYJ-7154 pre-pass to the host,
    # which also removes the server's only evidence that the check happened. The
    # job now asks for an `ambiguity_result` back and the submit reply ALWAYS
    # discloses a missing or `high` verdict. This flag turns that disclosure into
    # a REFUSAL.
    #
    # DEFAULT FLIPPED TO ON, 2026-08-29 (SHYJ-5692). The OFF rationale -- that a
    # refusal throws away a generation the tester already paid for -- assumed a
    # cost the refusal does not actually impose: it deletes no prep, drops no
    # staged category row and consumes no remediation round, so the tester runs
    # step 0 and resubmits the SAME suite under the SAME prep_id, at the price of
    # one round trip. Weighed against that, a disclosure buried in a reply a
    # summarising host model prunes did not stop the SHYJ-5692 run finalizing 80
    # cases against a ticket nothing had checked for being too under-specified to
    # test -- which is the SHYJ-7154 failure this preflight exists to prevent.
    # "No verdict" is also structurally different from "checked and found
    # nothing", and only a refusal keeps those two apart.
    # Still an `operator_choice`, and OFF remains legitimate per install: a team
    # whose tickets are written to a template, or one whose host client cannot be
    # relied on to return the field, is better served by the disclosure than by a
    # gate it will learn to route around. Setting
    # QA_HOST_AMBIGUITY_REQUIRE_RESULT=false restores the previous behaviour
    # exactly, with no code change.
    # Inert unless QA_HOST_AMBIGUITY_REVIEW_ENABLED actually shipped the job (it
    # is keyed off the prep's meta stamp, not off the flag, so a mid-flow flip
    # cannot change an in-flight prep).
    qa_host_ambiguity_require_result: bool = True

    # --- Refuse a host submit whose screens are off-topic / unjudged (OFF) --
    # Batch 4, LAYER 2 (2026-08-09). Mirrors the flag above exactly: the
    # disclosure is the honest-by-default behaviour, and an operator who
    # genuinely relies on the screens being right turns the REFUSAL on. ON, a
    # submission is refused at finalize when THIS prep asked for a relevance
    # verdict, actually forwarded screens (captured or chat-attested), and the
    # submission carries either a `relevant: "no"` verdict or NO usable verdict
    # at all.
    # `unsure` PASSES, with Batch 2's warning. Refusing on uncertainty punishes
    # the honest answer and teaches a host that `yes` is the cheap one, which
    # would silently destroy the whole signal; `no` and "nothing came back" are
    # unambiguous and are what the reported run actually produced.
    # Default OFF because it changes whether an ALREADY-GENERATED suite is
    # accepted -- the same reasoning that shipped QA_HOST_VOLUME_FLOOR_ENABLED
    # OFF -- and because the verdict is UNTRUSTED self-report derived partly
    # from attacker-influenceable pixels, so as a hard gate a hostile or
    # malformed field could refuse a perfectly good suite. The refusal is
    # cheap and reversible: the prep and every staged category row survive
    # ("Nothing was discarded"), no remediation round is consumed, and
    # `image_relevance_ack=true` on qa_submit_suite clears it on the SECOND
    # submit (two-beat, exactly like volume_floor_ack).
    # Inert unless the prep's own meta stamps say so: it is keyed off
    # `host_image_require_relevant` / `host_image_relevance` / the two image
    # counts, never off the live flag, so a mid-flow .env flip cannot change an
    # in-flight prep and an OLD envelope is untouched.
    qa_host_image_require_relevant: bool = False

    # --- Host duplicate review: flags DELETED 2026-08-12 ----------------
    # QA_HOST_CATEGORY_RESUBMIT_NOTE_ENABLED,
    # QA_HOST_CATEGORY_SHRINK_GUARD_ENABLED,
    # QA_HOST_IMAGE_DESCRIPTION_ENABLED and QA_HOST_DEDUP_REVIEW_ENABLED were
    # deleted as flags on 2026-08-12 (flag-surface reduction, batch 1): every
    # one of them had soaked ON, so their ON behaviour is now unconditional and
    # no .env value can change it. The duplicate REVIEW is always requested;
    # whether the reported groups are ACTED on is still the opt-in sub-flag
    # below. See docs/FEATURE_FLAGS.md -> "Changelog 2026-08-12".
    # Sub-flag: actually REMOVE the non-keeper members of each reported group.
    # Default OFF, and deliberately ASYMMETRIC with the embedding-based semantic
    # dedup path (which does remove -- itself hardcoded OFF on 2026-08-13,
    # flag-surface reduction batch 7): that path drops on a NUMERIC cosine >=
    # qa_semantic_dedup_threshold over a fixed payload, with a protected-id list
    # and the NB-016 sole-tracer rescue. A host model's free-form judgement has no
    # threshold and no calibrated precision, and -- unlike an embedding computed
    # server-side -- it arrives as UNTRUSTED input. The realistic threat is NOT an
    # untrustworthy host model: it is injected content inside the _GUARD-wrapped
    # Jira/comment text that host mode deliberately places in the host's own
    # context. So this path is DESTRUCTIVE + attacker-influenced and is bounded by
    # the two deterministic server-side screens below, in addition to defaulting
    # OFF and to the NB-016 rescue mirrored in agents/host_mode.py.
    qa_host_dedup_apply: bool = False
    # Caps on the UNTRUSTED field's SHAPE: how many groups are considered and how
    # many cases one group may name. Never-raise-coerced as positive ints and
    # additionally hard-capped in agents/host_mode.py, whose constant wins when it
    # is smaller. NB these are shape caps, not a safety bound: 50 x 12 = 550
    # removable ids, so they do NOT stop a disjoint partition of the suite. The two
    # ratios below are the actual bound.
    qa_host_dedup_max_groups: int = 50
    qa_host_dedup_max_group_size: int = 12
    # SAFETY BOUND (the one that actually bounds the destructive path). Max share of
    # the SUBMITTED cases one host review may remove; above it the WHOLE review is
    # refused (nothing removed) and the refusal is reported verbatim. An operator may
    # LOWER this; the module ceiling (0.40) in agents/host_mode.py wins, so it can
    # never be raised. Deliberately CORPUS-INDEPENDENT: it needs no calibration, so
    # it is a guarantee rather than a tuned guess. The second bound is a module
    # constant with no .env knob at all (_DUP_MAX_APPLY_GROUP_SIZE = 4: more than 4
    # cases in one "duplicate" cluster is a partition primitive, not a duplicate).
    qa_host_dedup_max_removal_ratio: float = 0.35
    # PRESENTATION ONLY -- not a bound. Groups whose server-measured textual
    # agreement falls below this are LABELLED "LOW, review before trusting" in the
    # reply. It gates nothing, because it cannot: MEASURED 2026-07-29, the
    # motivating cross-category duplicate scores 0.29 while two unrelated cases score
    # 0.28-0.34 and two cases that must NOT be merged score 0.95, so no threshold
    # separates the classes (see agents/host_mode.dup_agreements for the full table).
    # Shipping an uncalibrated number as a security bound would look like a
    # guarantee and not be one.
    qa_host_dedup_low_text_ratio: float = 0.5

    # --- Host-mode parallel chat fan-out ---------------------------------------
    # UNCONDITIONAL since 2026-08-13 (flag-surface reduction, batch 8a):
    # QA_HOST_PARALLEL_FANOUT_ENABLED was DELETED and hardcoded to `True` -- the
    # SHIPPED value of the public distribution .env template, NOT this field's
    # code default. qa_prepare_test_cases therefore always carries the
    # orchestration contract that lets the PARENT chat spawn one same-session
    # worker per category (Cursor Task / equivalent), always exposes
    # qa_prep_status, and always applies the empty-suite finalize completeness
    # gate. MCP cannot invoke host Task tools, so this only ever changed the
    # prepare payload, the instructions, that status tool and that gate.
    # Primary finalize (Path A, crash-safe -- 2026-07-31 incident):
    # qa_submit_category x N as each worker returns, then an empty
    # qa_submit_suite (+ optional acceptance_criteria/ambiguity_result sidecar),
    # gated until every expected category is staged. Fallback (Path B): merge in
    # the parent + ONE full qa_submit_suite -- which keeps the dedup/coverage
    # review (it needs the merged suite's global tc_ids) but is lost wholesale if
    # the chat dies before that single call, exactly how the first live run died.
    # The seam is agents.host_mode._parallel_fanout_on().
    # See docs/FEATURE_FLAGS.md.

    # --- Volume floor + duplicate-prepare guard: flags DELETED 2026-08-12 -
    # QA_HOST_VOLUME_FLOOR_ENABLED and QA_HOST_DUPLICATE_PREP_GUARD_ENABLED were
    # deleted as flags on 2026-08-12 (flag-surface reduction, batch 1); both had
    # soaked ON and are now unconditional. The floor is still always the prep's
    # OWN stamped value, the guard is still keyed on source_url only, both still
    # fail OPEN, and `volume_floor_ack=true` is still honoured only after a
    # refusal. The two WINDOW settings below stay as tuning knobs.
    qa_host_duplicate_prep_window_s: int = 1800
    # 2026-08-10 (I3): the FINISHED-SUITE half of the same guard gets its own,
    # much wider window. The two are different failure modes and 1800s was only
    # ever right for one of them: a second PREP 43 seconds later is wasted
    # in-flight work, while a second finalized SUITE hours later is a duplicate
    # deliverable the tester pays for twice. Live 2026-08-10: the 13:06 prepare
    # was the THIRD full generation of one ticket that day -- SEVEN stored
    # suites and a delivered .xlsx -- and said nothing, because the newest match
    # was ~4.9x the prep window old. A generous default is safe here because
    # this exit is INFORMATIONAL, not a refusal: it names the existing suite and
    # `proceed_anyway=true` still regenerates, so the worst case is one extra
    # confirmation round trip per ticket per day. Coerced by the same lenient,
    # never-raising validator as every other int in this file.
    qa_host_duplicate_suite_window_s: int = 86400

    # --- Server-LLM kill switch: DELETED 2026-08-15 ------------------
    # QA_SERVER_LLM_ENABLED and QA_SERVER_LLM_ALLOW were the host-boomerang
    # migration's rollout switch. That migration is done on the surface it
    # was built for: NO MCP tool reaches a server-side backend any more,
    # the last holdout (Feature Analysis screen descriptions) having moved
    # to the tester's own chat model on 2026-08-15. Under the flag policy a
    # soaked rollout flag is deleted with the behaviour hardcoded to the
    # value it SHIPPED -- here `true` and an empty allow-list -- so both
    # fields are gone and `llm.server_llm_enabled()` is a True constant.
    # A stale QA_SERVER_LLM_* line in an existing .env is ignored
    # (model_config uses extra="ignore"). Deleted rather than pinned OFF:
    # pinning OFF would suppress the paths that are NOT on the MCP surface
    # (evals/, the legacy graph.py/router.py coroutines), and deleting
    # those backends is the separate follow-up plan CLAUDE.md names, never
    # a drive-by. See docs/FEATURE_FLAGS.md -> "Changelog -- 2026-08-15".
    # --- Host-boomerang migration flags: DELETED 2026-08-12 ---------------
    # QA_HOST_RISK_REVIEW_ENABLED, QA_HOST_TEST_PLAN_REVIEW_ENABLED,
    # QA_HOST_CHECKLIST_REVIEW_ENABLED, QA_HOST_CHECKLIST_NLI_SUPPRESS_ENABLED
    # and QA_HOST_COMMENT_RECONCILE_SUPPRESS_ENABLED were MIGRATION flags, not
    # features, and every ledger row they governed is terminal. On 2026-08-12
    # they were deleted and their ON behaviour hardcoded: on the host path this
    # server makes no risk-scoring, test-plan, requirement-decomposition,
    # checklist NLI/adjudication or Jira comment-extraction call. Each was
    # then AND-ed with the pre-existing, default-OFF feature flag it rode on
    # -- and those five flags (QA_LLM_RISK_SCORING / QA_TEST_PLAN_ARTIFACTS /
    # QA_ATOMIC_CHECKLIST_ENABLED / QA_CHECKLIST_NLI_ENABLED /
    # QA_COMMENT_RECONCILE_ENABLED) were themselves DELETED on 2026-08-14
    # (batch 8b-ii) and hardcoded, the checklist ON and the other four OFF.
    # So there is no flag left to AND with: the switches are the named seams
    # listed in docs/FEATURE_FLAGS.md, and a default install now RUNS the
    # atomic checklist rather than opting out of it.
    # See docs/FEATURE_FLAGS.md -> "Changelog 2026-08-12" and
    # docs/LLM_MIGRATION_INVENTORY.md.
    # --- Prep crash-safety (2026-07-31 SHYJ-5645 incident) ----------------
    # The sliding TTL (qa_get_category_job / qa_submit_category refresh the
    # prep's TTL clock so an ACTIVE orchestration cannot expire mid-run) and the
    # unfinished-prep disclosure shipped as QA_PREP_SLIDING_TTL_ENABLED /
    # QA_PREP_DISCLOSE_UNFINISHED, both default ON since 2026-08-01. On
    # 2026-08-12 both flags were deleted and the behaviour hardcoded ON
    # (flag-surface reduction, batch 1). Activity can still never extend a prep
    # forever: total lifetime stays bounded by qa_prep_max_lifetime_s below.
    # Hard ceiling (seconds) on a prep's TOTAL lifetime under the sliding TTL.
    # Lenient never-raise int coercion like the rest; <=0 -> the default.
    qa_prep_max_lifetime_s: int = 14400

    # Append-only audit log (LT-1 ph2). SQLite file recording key events (suite
    # generated, exported, pushed, bug reported) so multi-team deployments have a
    # trail. Never-raise; a failure degrades to no-audit, logged.
    qa_audit_log_path: str = "data/audit.db"

    # MCP server (mcp_server.py) exposing the QA agents/tools to Claude
    # Desktop / Claude Code / Cursor over stdio — the product's primary
    # surface. Off by default like every other feature gate: turning it on
    # lets an MCP client drive generation, exports, and (dry-run-defaulted)
    # device runs -- each MCP tool call is separately audited.
    qa_mcp_enabled: bool = False

    # Guided, choice-driven MCP wizard (qa_wizard) + missing-parameter prompts
    # via MCP elicitation (ctx.elicit) -- UNCONDITIONAL since 2026-08-13
    # (flag-surface reduction, batch 7 (needs-config)): QA_MCP_ELICIT_ENABLED
    # was DELETED and the behaviour hardcoded to the value the DISTRIBUTION
    # ships (`true`) -- as do this repo's own .mcp.json and .cursor/mcp.json --
    # NOT this field's code default (`False`). The per-CLIENT limitation that
    # made a switch look necessary is handled WITHOUT one: Claude Desktop
    # advertises no elicitation capability, so ctx.elicit raises there, the
    # callback reports UNAVAILABLE and the caller falls back to the markdown
    # menu exactly as it did with the flag off. mcp_server._elicit_enabled() and
    # tools/mcp_handlers._elicit_enabled() return the True constant; every
    # dialog stays bounded by the existing per-call elicitation budget. See
    # docs/FEATURE_FLAGS.md.

    # GitHub-Release startup self-update (launcher.py -> tools/updater.py) --
    # REMOVED 2026-08-13 (flag-surface reduction, batch 6): QA_AUTO_UPDATE_ENABLED
    # was DELETED and hardcoded OFF, so a developer checkout's `python
    # launcher.py` never opts itself into a self-update. This does NOT change
    # the distribution: its generated launcher calls
    # run_update_check(force=True, ...), and that force argument bypasses the
    # deleted field exactly as it always did. See docs/FEATURE_FLAGS.md.
    # "owner/name" of the (private) GitHub repo to check for releases. Empty
    # disables the check even when a caller forces it.
    qa_update_repo: str = ""
    # GitHub token for the Releases API + zipball download. REQUIRED for a
    # private repo (fine-grained PAT, read-only Contents scope). Sent only as an
    # Authorization: Bearer header, never logged. .env only.
    github_token: str = ""
    # Bounded network timeout (seconds) for the release check + download.
    qa_update_timeout: int = 10
    # Integrity self-heal + read-only code lock (distribution installs) --
    # REMOVED 2026-08-13 (flag-surface reduction, batch 6): QA_CODE_LOCK_ENABLED
    # was DELETED and hardcoded OFF for a developer checkout. The distribution
    # is unaffected: its launcher passes lock_override=True, which takes
    # precedence exactly as it always did, so startup still verifies every
    # MANIFEST.sha256 entry, re-downloads locally-modified files and chmods code
    # read-only there. See docs/FEATURE_FLAGS.md.

    # Release-signature enforcement for auto-updates (Ed25519). When ON, an
    # update or self-heal is REFUSED unless the release ships a MANIFEST.sig
    # that verifies against the public key embedded in
    # tools/updater._RELEASE_PUBLIC_KEY_HEX. Default OFF for exactly ONE
    # migration release so installs predating signing still update (they log a
    # prominent unsigned-release warning); an INVALID signature is ALWAYS
    # rejected regardless of this flag. Flip ON (see runbook) once every live
    # release is signed. Lenient never-raising bool coercion like the rest.
    qa_update_require_signature: bool = False

    # --- Usage analytics (telemetry) - opt-out; ON only in the dist. ---
    # Anonymous usage metrics via tools/telemetry.py (PostHog). Sends only
    # when a PostHog key is present (POSTHOG_API_KEY / the dist's baked
    # default) AND no opt-out is set. It stays inert in the private checkout
    # (no key) and is turned ON only by the distribution build, with README
    # disclosure.
    #
    # 2026-08-13, and this one deserves reading twice: QA_TELEMETRY_DISABLED was
    # DELETED and hardcoded to its default, False (flag-surface reduction,
    # batch 6). There is now exactly ONE opt-out, the cross-vendor standard
    # DO_NOT_TRACK=1 environment variable, which tools/telemetry._opted_out()
    # still honours. Every document that offered two must name only that one --
    # an opt-out that silently does nothing is worse than no opt-out at all.
    # See docs/FEATURE_FLAGS.md.
    # Optional operator-set identity (known teams). When set it becomes the
    # PostHog distinct_id and person email; otherwise an anonymous install
    # UUID (qa_telemetry_id_path) is used. No other PII is ever collected.
    qa_user_email: str = ""
    # Where the anonymous install UUID is persisted (update-protected data dir).
    qa_telemetry_id_path: str = "data/telemetry-id"
    # PostHog project API key (a WRITE-ONLY public ingest key). Empty in the
    # private checkout; the distribution build bakes a default. Env overrides.
    posthog_api_key: str = ""

    @field_validator(
        "qa_api_test_enabled",
        "qa_testrail_push_enabled",
        "qa_xray_push_enabled",
        "qa_api_framework_write_enabled",
        "qa_api_framework_write_dry_run",
        "qa_ac_anchoring_enforce",
        "jira_fetch_comments",
        "jira_fetch_images",
        "jira_fetch_parent",
        "jira_fetch_sibling_stories",
        "qa_dist_mode",
        "qa_mcp_enabled",
        "qa_update_require_signature",
        "qa_host_ambiguity_require_result",
        "qa_host_image_require_relevant",
        "qa_host_dedup_apply",
        mode="before",
    )
    @classmethod
    def _coerce_bool(cls, v: object, info) -> bool:
        # The fallback is the field's OWN default, never a hard-coded False:
        # every *_DRY_RUN flag defaults to True, so a blank/mistyped value used
        # to disarm the very guard it should have preserved. A non-bool default
        # (or PydanticUndefined on a required field) degrades to False, which is
        # the historical behaviour.
        raw_default = getattr(cls.model_fields.get(info.field_name), "default", False)
        return _lenient_bool(
            v, info.field_name, raw_default if isinstance(raw_default, bool) else False
        )

    @field_validator("qa_rag_similarity_threshold", mode="before")
    @classmethod
    def _coerce_threshold(cls, v: object) -> float:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        try:
            return float(str(v).strip())
        except (TypeError, ValueError):
            logger.warning(
                "Invalid QA_RAG_SIMILARITY_THRESHOLD=%r — using default 0.3", v
            )
            return 0.3

    @field_validator("qa_semantic_dedup_threshold", mode="before")
    @classmethod
    def _coerce_semantic_threshold(cls, v: object) -> float:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        try:
            return float(str(v).strip())
        except (TypeError, ValueError):
            logger.warning(
                "Invalid QA_SEMANTIC_DEDUP_THRESHOLD=%r — using default 0.9", v
            )
            return 0.9

    @field_validator(
        "qa_checklist_match_high",
        "qa_checklist_min_granularity",
        "qa_rag_similar_min_score",
        mode="before",
    )
    @classmethod
    def _coerce_checklist_float(cls, v: object, info) -> float:
        """Lenient, never-raising float coercer for the Batch-2 checklist bands
        and the RAG relevance floor (QA_RAG_SIMILAR_MIN_SCORE, added
        2026-07-30 -- it belongs here rather than with the reconciler
        thresholds precisely BECAUSE this group clamps).

        Mirrors _coerce_jira_int: an unparseable value is logged and replaced
        with the field's declared default rather than raising.

        All four fields are similarity / quality SCORES, so the parsed value is
        additionally CLAMPED to [0.0, 1.0] -- the same kind of range guard
        _POSITIVE_INT_FIELDS gives the int caps. Without it, an operator writing
        QA_CHECKLIST_MATCH_HIGH=75 (meaning "75%") would silently push every
        requirement below the threshold and the report would claim 0% coverage
        for a perfectly good suite.
        """
        default = cls.model_fields[info.field_name].default
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            parsed = float(v)
        else:
            try:
                parsed = float(str(v).strip())
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid %s=%r — using default %s",
                    info.field_name.upper(),
                    v,
                    default,
                )
                return default
        if parsed < 0.0 or parsed > 1.0:
            clamped = min(1.0, max(0.0, parsed))
            logger.warning(
                "%s=%r is outside the valid [0, 1] range — clamping to %s",
                info.field_name.upper(),
                parsed,
                clamped,
            )
            return clamped
        return parsed

    @field_validator(
        "qa_host_dedup_max_removal_ratio",
        "qa_host_dedup_low_text_ratio",
        mode="before",
    )
    @classmethod
    def _coerce_reconcile_threshold(cls, v: object, info) -> float:
        """Lenient, never-raising float coercion for the host-dedup ratios.

        The NAME is historical: it was written for
        qa_comment_reconcile_field_threshold / _dedup_threshold, which were
        deleted on 2026-08-15 with tools/comment_reconciler.py (dead-code
        deletion batch D5). Kept rather than renamed because
        _coerce_token_price's docstring below refers to it by name and the two
        surviving fields' coercion behaviour is unchanged.
        """
        default = cls.model_fields[info.field_name].default
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        try:
            return float(str(v).strip())
        except (TypeError, ValueError):
            logger.warning(
                "Invalid %s=%r — using default %s",
                info.field_name.upper(),
                v,
                default,
            )
            return default

    @field_validator("qa_rag_top_k", mode="before")
    @classmethod
    def _coerce_top_k(cls, v: object) -> int:
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            logger.warning("Invalid QA_RAG_TOP_K=%r — using default 5", v)
            return 5

    @field_validator(
        "jira_max_comments",
        "jira_max_images",
        "jira_max_image_bytes",
        "jira_max_parent_chars",
        "jira_max_sibling_chars",
        "jira_max_sibling_stories",
        "qa_device_command_timeout",
        "qa_device_screenshot_timeout",
        "qa_rag_recency_half_life_days",
        "qa_rag_max_entries",
        "qa_update_timeout",
        "qa_checklist_max_items",
        "qa_checklist_max_prompt_chars",
        "qa_prep_ttl_s",
        "qa_prep_max_bytes",
        "qa_prep_max_lifetime_s",
        "qa_host_dedup_max_groups",
        "qa_host_dedup_max_group_size",
        "qa_host_duplicate_prep_window_s",
        "qa_host_duplicate_suite_window_s",
        mode="before",
    )
    @classmethod
    def _coerce_jira_int(cls, v: object, info) -> int:
        default = cls.model_fields[info.field_name].default
        if isinstance(v, int) and not isinstance(v, bool):
            parsed = v
        else:
            try:
                parsed = int(str(v).strip())
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid %s=%r — using default %d",
                    info.field_name.upper(),
                    v,
                    default,
                )
                return default
        if info.field_name in _POSITIVE_INT_FIELDS and parsed < 1:
            logger.warning(
                "Invalid %s=%r (must be > 0) — using default %d",
                info.field_name.upper(),
                parsed,
                default,
            )
            return default
        return parsed


# Bound on the degrade-and-retry loop below. Pydantic reports every field error
# in one pass, so a real environment converges in a single round; the bound only
# exists so a pathological case cannot spin.
_MAX_DEGRADE_ROUNDS = 12


def _offending_fields(exc: ValidationError) -> list[str]:
    """Names of the Settings fields pydantic rejected. Never raises."""
    names: list[str] = []
    try:
        for err in exc.errors():
            loc = err.get("loc") or ()
            if loc and isinstance(loc[0], str) and loc[0] in Settings.model_fields:
                names.append(loc[0])
    except Exception:  # pragma: no cover - defensive
        logger.debug("could not read validation errors", exc_info=True)
    return names


def _load_settings() -> Settings:
    """Build Settings, degrading FIELD BY FIELD rather than all-or-nothing.

    The per-field coercers above already make a bad value non-fatal for every
    field they cover. This backstop handles what they cannot: a field with no
    coercer, or a value pydantic rejects before any validator runs.

    It used to fall straight back to ``Settings.model_construct()``, which
    resets EVERY field to its class default — so one unusable value silently
    discarded the operator's Jira credentials, export directory and every flag,
    logging only a warning while the surviving defaults looked plausible enough
    to hide it. Instead, pin just the offending fields to their declared
    defaults (init kwargs outrank the environment) and retry, so a bad value
    costs one field. ``model_construct()`` remains the last resort if even that
    cannot converge (I-045 / B-017).
    """
    overrides: dict[str, object] = {}
    degraded: list[str] = []
    for _ in range(_MAX_DEGRADE_ROUNDS):
        try:
            loaded = Settings(**overrides)
        except ValidationError as exc:
            fresh = [n for n in _offending_fields(exc) if n not in overrides]
            if not fresh:
                logger.warning(
                    "Settings rejected the environment and the offending "
                    "field(s) could not be identified (%s).",
                    exc,
                )
                break
            for name in fresh:
                overrides[name] = Settings.model_fields[name].get_default(
                    call_default_factory=True
                )
                degraded.append(name)
            continue
        except Exception as exc:  # pragma: no cover - defensive backstop
            logger.warning("Settings failed to load (%s).", exc)
            break
        if degraded:
            logger.warning(
                "Settings: %d field(s) had an unusable value and were reset to "
                "their defaults (%s). Every other field kept its configured "
                "value.",
                len(degraded),
                ", ".join(sorted(degraded)),
            )
        return loaded
    logger.warning(
        "Settings could not parse the environment even after resetting %s — "
        "falling back to built-in defaults for ALL fields.",
        ", ".join(sorted(degraded)) or "nothing",
    )
    return Settings.model_construct()


settings = _load_settings()

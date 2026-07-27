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
# (langsmith / langgraph) also see the values. BaseSettings additionally reads
# the same file for its own declared fields below.
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
#     jira_max_image_bytes, qa_max_chat_images, qa_max_chat_image_bytes,
#     qa_max_spec_bytes, qa_max_spec_chars) — 0 there is a legitimate
#     "allow none" cap; bounding them would be a behaviour change out of scope
#     for this hygiene batch.
_POSITIVE_INT_FIELDS = frozenset(
    {
        "qa_checklist_max_items",
        "qa_checklist_max_prompt_chars",
        "qa_checklist_max_pairs",
        "qa_llm_timeout_s",
        "qa_device_command_timeout",
        "qa_device_screenshot_timeout",
        "qa_maestro_run_timeout",
        "qa_maestro_explore_step_timeout",
        "qa_maestro_translate_concurrency",
        "qa_maestro_heal_max_attempts",
        "qa_maestro_explore_max_steps",
        "qa_coverage_regen_max_rounds",
        "qa_update_timeout",
        "qa_web_run_max_cases",
        "qa_web_run_vision_budget",
        "qa_web_run_timeout_s",
        "qa_comment_reconcile_max_comments",
        "qa_comment_reconcile_max_amendments",
        "qa_llm_max_tokens_category",
        "qa_llm_max_tokens_critic",
        "qa_llm_max_tokens_rewrite",
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

    # LLM backend selection — see llm.py.
    #   "cli"    : drive the Claude CLI OAuth session via subprocess (no API key needed).
    #   "api"    : call the Anthropic API directly (needs ANTHROPIC_API_KEY).
    #   "cursor" : drive the `cursor-agent` CLI via subprocess (needs CURSOR_API_KEY).
    #              SECURITY NOTE: cursor-agent has no flag to fully disable tool
    #              use in headless mode (unlike the "cli" backend's
    #              --disallowedTools '*'), so llm.py sandboxes each call to a
    #              disposable temp directory to contain any stray write/command.
    qa_llm_backend: str = "cli"

    # Used by the "api" backend. The "cli" backend ignores this and strips it
    # from the subprocess environment so it always uses the CLI OAuth session.
    anthropic_api_key: str = ""

    # Used by the "cursor" backend (needs the `cursor-agent` CLI installed).
    cursor_api_key: str = ""

    # Model id used by both backends (CLI --model flag / API model field).
    qa_llm_model: str = "claude-sonnet-4-6"
    # Per-LLM-call timeout in seconds (llm.py kills the subprocess beyond it).
    # 120 suits dev; distribution installs ship 300 in .env (grounded prompts
    # + concurrent category fan-out through a local CLI can run long).
    qa_llm_timeout_s: int = 120

    # Model id for the "cursor" backend (e.g. "sonnet-4", "gpt-5"). Uses
    # cursor-agent's own model naming, which differs from qa_llm_model's.
    qa_cursor_model: str = "sonnet-4"

    # Fallback model the "cursor" backend switches to on a category's 2nd+ retry
    # after a CursorAgentError (e.g. "Agent Looping Detected"). That error's own
    # message literally suggests "try again with a different model" — some
    # prompt/model combinations loop deterministically on every retry with the
    # SAME model, so switching breaks the pattern instead of repeating it
    # verbatim. Must be a valid `cursor-agent models` id (plain "gpt-5" is NOT
    # one). The "-fast" variant is deliberate: plain "gpt-5.2" was observed to
    # run noticeably slower than sonnet-4 under --sandbox enabled, causing
    # TimeoutError on the retry that was meant to rescue the category. Empty
    # string disables the switch (keeps retrying qa_cursor_model).
    qa_cursor_fallback_model: str = "gpt-5.2-fast"
    # Strict host-matched auto backend (QA_LLM_STRICT_HOST, default ON). When
    # QA_LLM_BACKEND=auto, honour ONLY the account of the host editor the tester
    # is working in — Cursor -> cursor-agent, Claude Code/Desktop -> claude CLI —
    # and NEVER silently fall through to a different backend/account when the
    # host's own is present-but-unauthenticated (llm.py then fails fast with an
    # actionable message instead of hanging on a 120s timeout). OFF restores the
    # legacy first-available fallback as an escape hatch.
    qa_llm_strict_host: bool = True

    # Cheaper/faster model for the intent router's classification pass (T-04 /
    # I-027). Empty string means "use qa_llm_model" (no override). Set to a haiku
    # model to cut routing cost — the classifier is a tiny, low-stakes call.
    qa_classifier_model: str = "claude-haiku-4-5"

    # ---- Structured JSON via forced tool use (api backend only) -------------
    # QA_STRUCTURED_JSON_ENABLED, default OFF (constitution: new behaviour is
    # opt-in). When ON, llm.ask_json stops asking the model for JSON in prose and
    # instead compiles the pydantic response_model's JSON schema into an
    # Anthropic TOOL input_schema, forcing that one tool with tool_choice. The
    # API then returns tool_use.input as an already-parsed dict, so on that
    # branch a JSONDecodeError is structurally impossible -- which matters because a
    # single parse failure today re-runs an ENTIRE test-case category
    # (agents/test_scenario_agent.py's _RETRYABLE path: one full ~110s call).
    # Pydantic still validates semantics, so a genuinely wrong field is still
    # caught and still retried exactly as before.
    # Ignored by the cli/cursor backends: they drive a subprocess that has no
    # tool API, so they keep the JSON-in-prompt path byte-for-byte unchanged.
    qa_structured_json_enabled: bool = False
    # QA_STRUCTURED_JSON_STRICT, default OFF. Adds "strict": true to the tool
    # definition, which switches the provider to CONSTRAINED DECODING (the schema
    # becomes a grammar) instead of mere schema guidance. It is a second flag
    # rather than part of the first because strict mode is model-gated and
    # schema-fussy: it needs additionalProperties:false on every object node and
    # rejects pattern / minLength / maxLength / minimum / maximum / minItems,
    # all of which tools/models.py's TestCase uses. llm.py sanitises the schema
    # and only sends strict on models known to support it; an API rejection is
    # memoised per (model, schema) and degrades to non-strict forced tool use.
    qa_structured_json_strict: bool = False

    # ---- Anthropic prompt caching for the category fan-out (api backend) ----
    # QA_PROMPT_CACHE_ENABLED, default OFF (constitution: new behaviour is
    # opt-in). When ON, agents/test_scenario_agent.py hoists the per-category
    # instruction OUT of the system prompt into a small trailing user block, so
    # all 8 concurrent category calls share ONE byte-identical cached prefix
    # (system + the whole grounded user context). Cache reads bill 0.10x input,
    # writes 1.25x, with a 5-minute ephemeral TTL that every read refreshes.
    # This changes the PROMPT STRUCTURE, which is exactly why it is a flag:
    # OFF, the assembled prompt is byte-identical to the pre-cache path on all
    # three backends. Ignored entirely by the cli/cursor backends.
    qa_prompt_cache_enabled: bool = False
    # Minimum cacheable prefix in TOKENS. 0 = derive it from the model, using
    # llm.py's published table (4096 for opus 4.5-4.8 and haiku 4.5, 2048 for
    # sonnet 4.6 and haiku 3/3.5, 1024 for sonnet 3.7-4.5; an unrecognised id
    # takes the conservative 4096). Set this explicitly only for a model whose
    # minimum llm.py does not know. Below the minimum a cache_control marker is
    # silently ignored by the provider — no error, just a wasted 1.25x write —
    # so llm.py sends a plain unmarked block instead.
    qa_prompt_cache_min_tokens: int = 0
    # max_tokens for the cache warm-up request that runs BEFORE the fan-out.
    # 0 is correct and free: it runs prefill only (writing the cache), returns
    # an empty content list with stop_reason "max_tokens" and bills zero output
    # tokens. Raise it to 1 only if a future API version rejects 0.
    qa_prompt_cache_warm_max_tokens: int = 0

    # Model tiering for non-generation call sites (default OFF -- preserves
    # today's sonnet behaviour byte-for-byte). AC synthesis
    # (tools/rtm.generate_acs) already uses qa_classifier_model unconditionally
    # and needs no flag; these two sites are opt-in because their output is
    # user-visible free text / generated Maestro commands rather than an
    # internal structured critique. See .claude/plans/plan-model-tiering.md for
    # the eval-gated rollout and the >5pp quality-regression rollback rule.
    qa_model_tiering_enabled: bool = False

    # Per-site override -- "default" follows qa_model_tiering_enabled, "haiku"
    # forces the cheap model regardless of the master flag, "sonnet" forces
    # today's model regardless of the master flag (a per-site kill-switch that
    # doesn't require flipping the master off for every site).
    qa_model_tier_coverage_gaps: str = "default"
    qa_model_tier_maestro_translate: str = "default"

    jira_base_url: str = ""
    jira_api_token: str = ""
    jira_email: str = ""
    # Jira access pre-flight (QA_JIRA_PREFLIGHT). Default **ON** — a deliberate
    # exception to the constitution's defaults-OFF rule, with the same
    # precedent as the ambiguity gate and QA_AUTO_EXPORT_XLSX: before fetching a
    # Jira ticket URL on the MCP path the handler live-probes
    # /rest/api/3/myself, so a missing/invalid credential set returns guided
    # setup steps instead of a suite fabricated from an empty anonymous Jira
    # SPA shell. Set QA_JIRA_PREFLIGHT=false to disable the probe (kill-switch).
    qa_jira_preflight: bool = True
    # Jira custom-field id that holds Acceptance Criteria. Defaults to the common
    # Jira Software default; different instances use different ids, so make it
    # configurable (QW-11 / I-023 / B-015). When empty on a ticket, jira_fetcher
    # falls back to scanning the description for an "Acceptance Criteria" heading.
    jira_ac_field: str = "customfield_10016"
    # Parent-story context (JIRA_FETCH_PARENT). Default **ON** — the THIRD
    # deliberate exception to the constitution's defaults-OFF rule, alongside
    # QA_AUTO_EXPORT_XLSX and QA_AMBIGUITY_GATE_SEVERITY / QA_JIRA_PREFLIGHT.
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
    jira_max_parent_chars: int = 1500

    # Ticket comments are a second REST call (/issue/{key}/comment). Off by
    # default, same as this file's other opt-in fetches — flip on in .env once
    # validated, so the base issue fetch's existing behaviour (and every test
    # exercising it) is unaffected until an operator explicitly wants it.
    jira_fetch_comments: bool = False
    jira_max_comments: int = 5

    # --- Comment reconciliation (Batch 1) — opt-in, default OFF. -----------
    # A Jira description is a snapshot taken at refinement; the requirements
    # that are actually current accumulate in the comment thread. When ON,
    # tools/comment_reconciler runs a three-stage pipeline (Python noise filter
    # -> ONE quarantined extraction call -> deterministic Python resolution)
    # and the MCP handler injects a fenced AMENDMENTS block carrying only the
    # winners, with provenance emitted mechanically in code. While ON,
    # tools/jira_fetcher also SUPPRESSES the raw "## Comments" dump from
    # raw_text, so that block is the only comment-derived input the generation
    # model sees. OFF = zero extra LLM calls and a byte-identical prompt.
    qa_comment_reconcile_enabled: bool = False
    # Hard cap on comments handed to Stage 1 (the NEWEST N are kept). NOTE the
    # interaction with jira_max_comments above (default 5): while the
    # reconciler is ON, tools/jira_fetcher._effective_comment_cap requests
    # max(jira_max_comments, this) so the window here is real instead of being
    # silently clamped to 5; the filter then keeps the newest N of what arrived.
    qa_comment_reconcile_max_comments: int = 50
    # Comma-separated bot names, matched WHOLE-TOKEN against the author's
    # display name (see tools/comment_reconciler._is_bot_author) — never as a
    # substring, so "bot" drops "Release Bot" but keeps a reviewer named
    # "Bothaina" or "Talbot". Empty falls back to the module's built-in list.
    qa_comment_reconcile_bot_authors: str = (
        "jira-automation,automation for jira,github-actions,dependabot,"
        "depbot,renovate,bot"
    )
    # difflib ratio a comment's inferred field key must reach against the
    # ticket's own vocabulary before an amendment is applied. Below it the
    # candidate is FLAGGED for clarification instead of being applied to a
    # guessed field.
    qa_comment_reconcile_field_threshold: float = 0.90
    # Cosine similarity at/above which two additions count as the same
    # requirement (only used when an embeddings backend is configured;
    # otherwise normalised-string equality applies).
    qa_comment_reconcile_dedup_threshold: float = 0.92
    # Cap on amendments rendered into the block (newest survive).
    qa_comment_reconcile_max_amendments: int = 12
    # Character cap on the rendered block. 0 means "emit no block" (same
    # convention as jira_max_parent_chars), which is why this field is
    # deliberately NOT in _POSITIVE_INT_FIELDS.
    qa_comment_reconcile_max_chars: int = 1500

    # Image attachments require a second, authenticated download per image
    # PLUS a vision-capable LLM call (llm.ask_vision(), api backend only) to
    # turn them into text before they can reach the (text-only) cli/cursor
    # generation backends. Off by default: it's the most expensive/slowest
    # addition here and needs ANTHROPIC_API_KEY regardless of QA_LLM_BACKEND.
    jira_fetch_images: bool = False
    jira_max_images: int = 3
    jira_max_image_bytes: int = 5_000_000  # Anthropic's own per-image vision cap

    # Direct chat image uploads (screenshots/mockups attached to a message,
    # independent of Jira) — see tools/image_description.py -> llm.ask_vision()
    # (api backend only, same pipeline as Jira ticket images). These two caps
    # bound what the pipeline will actually read from an uploaded image.
    qa_max_chat_images: int = 3
    qa_max_chat_image_bytes: int = 5_000_000  # Anthropic's own per-image vision cap
    # Mobile device capture -> test cases (opt-in, off by default like the other
    # feature gates above). When ON, testers can list attached Android/iOS
    # devices, pick one, and generate test cases from a captured screenshot.
    # NOTE: the screenshot is analysed via llm.ask_vision(), which needs
    # ANTHROPIC_API_KEY regardless of QA_LLM_BACKEND (vision is decoupled from
    # the text backend -- see llm.ask_vision).
    qa_mobile_capture: bool = False
    # Timeout (seconds) for device-discovery commands (adb devices / simctl list).
    qa_device_command_timeout: int = 20
    # Timeout (seconds) for a single screenshot capture (larger -- image transfer).
    qa_device_screenshot_timeout: int = 60

    # --- Mobile Device Testing (Maestro) — opt-in, all default OFF / dry-run ON. ---
    # Gates the "📱 Mobile Testing" starter chip, the guided wizard, and the
    # "maestro" export keyword/button. Off by default per the constitution.
    qa_maestro_enabled: bool = False
    # Runner PRINTS the command instead of executing on a device when ON (default).
    qa_maestro_dry_run: bool = True
    # Maestro CLI binary, flow output directory, and per-run timeout (seconds).
    qa_maestro_binary: str = "maestro"
    qa_maestro_flow_dir: str = "maestro_flows"
    qa_maestro_run_timeout: int = 600
    # AI-assisted fail→diagnose→patch→rerun heal loop (mode c). Off by default; uses
    # llm.ask_vision on the failure screenshot, bounded by max attempts.
    qa_maestro_heal_enabled: bool = False
    qa_maestro_heal_max_attempts: int = 2
    # AI exploratory run (Layer 3 -- observe->decide->act). Off by default; adds
    # a 4th "🧭 AI exploratory run" mode to the Mobile Testing wizard,
    # bounded by a step budget. Each per-step device action honours
    # QA_MAESTRO_DRY_RUN (default ON).
    qa_maestro_explore_enabled: bool = False
    qa_maestro_explore_max_steps: int = 15
    qa_maestro_explore_step_timeout: int = 60
    # LLM step translation (Layer 1 upgrade) -- opt-in. When ON, the Maestro
    # exporter converts each test case's natural-language steps into concrete,
    # whitelist-validated Maestro commands (one llm.ask_json call per case,
    # bounded concurrency). When OFF the exporter keeps its skeleton behaviour.
    qa_maestro_translate_enabled: bool = False
    qa_maestro_translate_concurrency: int = 3
    # Test-account credentials for the Maestro login/recovery subflow. Injected as
    # Maestro env vars at RUN time (never written into YAML). .env only.
    qa_test_user: str = ""
    qa_test_password: str = ""

    # --- Web Suite Execution -- opt-in, default OFF / dry-run ON. ---
    # Runs a generated suite step-by-step against a live web app in a real
    # browser (tools/web_runner.py, the web analogue of the Maestro run
    # pipeline) and reports pass/fail per TC-ID. Off by default per the
    # constitution; the browser is launched through the same SSRF-hardened
    # path as tools/browser_renderer.py.
    qa_web_run_enabled: bool = False
    # Dry-run (default ON): translate + validate + report the PLANNED browser
    # actions WITHOUT launching a browser or spending a vision call.
    qa_web_run_dry_run: bool = True
    # Max cases executed per run (bounds cost + wall time).
    qa_web_run_max_cases: int = 20
    # Max ask_vision screenshot judgments per run (the text assertion runs
    # first; vision is only a bounded fallback when it is inconclusive).
    qa_web_run_vision_budget: int = 5
    # Per-browser-action timeout (seconds).
    qa_web_run_timeout_s: int = 60

    # LangSmith — read directly by langsmith/langgraph from the environment;
    # mirrored here for visibility.
    langchain_api_key: str = ""
    langchain_project: str = "qa-agents"

    # Web search grounding — disabled by default.
    qa_web_search_enabled: bool = False

    # Structured coverage critic + remediation (T-08). When ON, after the initial
    # fan-out a structured critique runs and, if gaps are found, ONE supplemental
    # generation pass fills them (merged + re-deduped + re-scored). Off by default
    # so the flagship generation path is unchanged until validated via the eval
    # harness (T-12).
    qa_coverage_regen_enabled: bool = False
    # Bound on the critic->generate remediation loop above (was a hardcoded
    # module constant _MAX_REMEDIATION_ROUNDS = 3). Default lowered to 2:
    # published self-critique research shows gains flatten after 2-3 rounds and
    # an unbounded/over-long loop can compound errors rather than improve
    # coverage (.claude/reports/research/testgen-accuracy-techniques-2026.md
    # section 1). Only a floor is enforced (>=1, via _POSITIVE_INT_FIELDS) --
    # no code-level ceiling, matching this file's existing convention for other
    # bounded-loop-count fields (qa_maestro_heal_max_attempts,
    # qa_maestro_explore_max_steps), which are likewise floor-only. See
    # .claude/plans/plan-remediation-cap.md.
    qa_coverage_regen_max_rounds: int = 2
    # Merge the critique + gap-fill generation into ONE ask_json call per round
    # instead of two sequential calls (critique_coverage then a full
    # _generate_for_category pass). Off by default: the existing two-call
    # behaviour is preserved byte-for-byte until this is validated. See
    # .claude/plans/plan-remediation-cap.md for the model-choice tradeoff (the
    # merged call resolves to qa_llm_model, NOT qa_classifier_model, because its
    # output includes tester-facing generated test cases, not just an internal
    # critique).
    qa_coverage_regen_merge_calls: bool = False
    # Chain-of-Thought reasoning stage per category (Feature 1 / CoT). When ON,
    # each category's single ask_json call is asked to FIRST enumerate what to test
    # (fields, limits, risks, attack vectors) into an internal ``analysis`` field,
    # THEN derive its test_cases from that reasoning -- one call, no extra
    # round-trip. ``analysis`` is discarded after generation (never shown to
    # testers). Off by default: when OFF the assembled prompt and the response model
    # are byte-identical to the pre-feature path. Adds only output tokens, so mind
    # the cli/cursor per-category timeout (_CATEGORY_TIMEOUT).
    qa_cot_reasoning_enabled: bool = False

    # Terse category-generation output (opt-in, default OFF -- see
    # .claude/plans/plan-terse-schemas.md). Every category ask_json call fills
    # the WHOLE TestCase schema, including fields that are ALWAYS overwritten
    # after generation regardless of what the model writes -- risk_score/
    # risk_label/risk_rationale are unconditionally replaced by
    # tools.risk_scorer.score_and_sort / score_with_llm on every path -- or
    # never read by any exporter -- postconditions is accepted by every
    # exporter's schema but rendered by NONE of them (xlsx/csv/gherkin/
    # playwright/testrail/xray/maestro all skip it; only "preconditions" is
    # rendered anywhere). When ON, the category system prompt tells the model
    # to leave those fields at their schema default and keep the fields that
    # DO matter to one concise sentence, cutting real output tokens with ZERO
    # relaxation of the existing anti-vagueness rules and ZERO effect on any
    # exported artifact's column layout. NOTE (rollout): the flag DOES change
    # the generated WORDING of title/steps/test_data/expected_result, and
    # tools/models._compute_stable_id hashes exactly those fields -- so
    # flipping this flag re-keys every content-derived stable_id (SID-*),
    # which dedup, suite_store persistence, TMS push and Zephyr External
    # IDs all key off. Treat enabling it like a suite re-baseline. OFF =
    # the assembled category prompt is byte-identical to today.
    qa_terse_category_output_enabled: bool = False

    # Per-call-type max_tokens ceiling (opt-in master flag, default OFF -- see
    # .claude/plans/plan-terse-schemas.md). llm.py hard-codes ONE ceiling
    # (_MAX_TOKENS = 16384) for every api-backend call, from the 8-category
    # fan-out (which legitimately needs headroom for up to 15 detailed cases)
    # down to the coverage critic and the vague-step rewriter (whose real
    # output is a handful of short strings). OFF = every call keeps today's
    # single 16384 ceiling. ON = category-class calls are unaffected
    # (qa_llm_max_tokens_category defaults to the SAME 16384) while
    # critic/rewrite-class calls get a much lower ceiling
    # (qa_llm_max_tokens_critic / qa_llm_max_tokens_rewrite) as a defensive
    # circuit breaker against a pathological repeating/looping generation on a
    # call that should never legitimately need more than a few hundred output
    # tokens. NOTE: max_tokens bounds worst-case spend/latency -- it does NOT
    # itself reduce the tokens billed for a normal-sized response, since
    # billing is on tokens actually generated, not the ceiling.
    qa_max_tokens_tiering_enabled: bool = False
    qa_llm_max_tokens_category: int = 16384
    qa_llm_max_tokens_critic: int = 4096
    qa_llm_max_tokens_rewrite: int = 4096

    # Spec-mutation eval axis (Feature 2). When ON, tools/eval_runner.mutation_score
    # asks an LLM for N behavioural mutations of a feature spec and judges, per
    # mutant, whether any generated test case would catch it (mutants_killed /
    # total). Makes REAL LLM calls, so it is gated here and never runs in the default
    # ``pytest tests/`` gate (the live path lives in evals/, marked ``eval``). Off by
    # default; never-raise (a failure yields a neutral, omitted score).
    qa_mutation_eval_enabled: bool = False

    # Enterprise Feature Analysis Report (opt-in, off by default). When ON,
    # generate_test_scenarios runs ONE extra structured LLM pass (text backend,
    # via llm.ask_json) that merges the Jira ticket content with any screenshot
    # descriptions into a full "Feature Analysis Report" (feature summary,
    # requirement analysis, UI analysis, user flow, missing requirements, risks)
    # and prepends it to the generation summary -- in ADDITION to the normal
    # test-case suite. Screenshot content still requires ANTHROPIC_API_KEY for
    # vision (image description is unchanged); with no screenshots the report is
    # built from Jira/feature text alone. Never breaks generation: a failure just
    # omits the report.
    qa_feature_analysis_enabled: bool = False

    # Token/cost meter (T-05): append an estimated-token cost line to the
    # generation summary. Disable to hide it.
    qa_token_meter_enabled: bool = True

    # Ambiguity pre-pass (T-11): minimum severity ("low"/"medium"/"high") at which
    # the app pauses to offer clarifying questions before generating. "off"
    # disables the pre-pass entirely (no extra LLM call).
    #
    # SHYJ-7154: this same flag ALSO gates the non-interactive MCP path, where it
    # is DELIBERATELY default-ON — the second intentional exception to the
    # defaults-OFF rule (after QA_AUTO_EXPORT_XLSX). Rationale: this gate exists
    # precisely to stop the confirmed P0 — confidently-wrong suites (fabricated
    # portals/endpoints) shipping to non-technical testers from under-specified /
    # no-UI tickets. Rollout impact: existing qa-agent-pro installs will, with NO
    # config change, receive clarifying questions (not a suite) for high-severity
    # under-specified tickets until the caller re-invokes with proceed_anyway=true.
    # Operator kill-switch: set QA_AMBIGUITY_GATE_SEVERITY=off.
    qa_ambiguity_gate_severity: str = "high"

    # AC anchoring (SHYJ-7154 Fix 3): when the source ticket carries REAL
    # (source-parsed) acceptance criteria, drop generated cases that cite a
    # NON-EXISTENT AC id (hallucinated traceability). Default OFF — the advisory
    # "AC Anchoring" warning section is always shown; only the dropping is gated.
    qa_ac_anchoring_enforce: bool = False

    # Test-plan artifacts (house rule: opt-in, default OFF). When ON, the
    # test-generation pipeline builds an AC-Validation report (only when the
    # ticket carried REAL source acceptance criteria) and a Test Plan / Strategy
    # section — at most two extra ask_json calls — rendered into the summary and
    # added as extra XLSX sheets. OFF = zero extra LLM calls.
    qa_test_plan_artifacts: bool = False

    # LLM-based risk scoring (opt-in, default OFF). When ON, generate_test_scenarios
    # replaces the deterministic priority×type heuristic with ONE batched ask_json
    # call that judges each case's business risk (business impact, blast radius,
    # data-loss potential, exploitability). Any failure (LLM error/timeout/missing
    # ids) falls through to the existing heuristic — never fails, never drops a
    # case. OFF = zero extra LLM calls and the heuristic path is byte-identical.
    qa_llm_risk_scoring: bool = False

    # Test-data strategy (opt-in, default OFF). When ON, the per-category
    # generation prompt asks the model to populate each case's ``test_data`` plan
    # (which fields need unique-per-run / seed-account / chained / static values,
    # each with a SAFE fake example and no real-looking PII), and the exporters +
    # chat summary render a Test Data column/note. OFF = prompt byte-identical, any
    # emitted test_data stripped before render, every export byte-identical to today.
    qa_test_data_strategy: bool = False

    # Surgical quality retry (opt-in, both default OFF -- see
    # .claude/plans/plan-surgical-retry.md). The per-category quality gate
    # (tools/quality_checks.quality_ratio) flags a category whose steps are
    # >30% vague/placeholder; by default it re-runs the ENTIRE category once
    # with a stricter reminder appended. These two independent flags change
    # that:
    #
    # qa_quality_reminder_upfront: fold the stricter reminder into EVERY
    # category's FIRST prompt instead of only on a triggered retry. Cheapest
    # fix when it prevents the retry outright (zero extra calls); costs ~230
    # extra words on every category's first prompt even when that category
    # was never going to need a retry. OFF = first-attempt prompt byte-
    # identical to today.
    qa_quality_reminder_upfront: bool = False

    # qa_surgical_quality_retry: when a retry is still needed, repair ONLY the
    # flagged test cases (a smaller ask_json call carrying just those cases +
    # targeted feedback, merged back by stable_id) instead of regenerating the
    # whole category from scratch. OFF = the existing full-category retry
    # runs unchanged.
    qa_surgical_quality_retry: bool = False

    # RAG corpus grounding — disabled by default.
    qa_rag_enabled: bool = False
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
    # Master gate for intra-suite semantic dedup (opt-in, default OFF). Required
    # IN ADDITION to an embeddings backend so enabling embeddings purely for RAG
    # ranking never silently starts DROPPING near-duplicate cases. OFF => the
    # generation pipeline never merges cases on embedding similarity.
    qa_semantic_dedup_enabled: bool = False

    # --- Atomic Requirements Checklist (Batch 2; opt-in, default OFF) ------
    # Master gate for the three-pass auditable-coverage pipeline:
    #   Pass 1  tools/atomic_checklist.decompose_to_checklist -> an unbounded,
    #           EARS-shaped, source-tagged checklist of every independently-
    #           verifiable outcome (ONE extra ask_json, run inside the existing
    #           concurrent enrichment gather so it costs no extra wall clock).
    #   Pass 2  the 8-category fan-out with that checklist injected as its OWN
    #           untrusted, CLUSTERED block (constraint-decay mitigation).
    #   Pass 3  tools/rtm.match_checklist -> a DETERMINISTIC EXTERNAL matcher
    #           that recomputes coverage instead of trusting the generating
    #           model's self-assigned requirement_id.
    # OFF => zero extra LLM calls, zero extra embedding work, and every summary
    # and export is byte-identical to the pre-feature output.
    qa_atomic_checklist_enabled: bool = False
    # Tier (b) of the matcher: ONE batched entailment judgement over the
    # ambiguous similarity band. Deliberately NOT a BERT-large NLI dependency --
    # a compact ask_json call with a strict judging prompt that is DIFFERENT from
    # the generator's, so the model still never marks its own homework.
    # OFF => the ambiguous band is simply reported as uncovered.
    qa_checklist_nli_enabled: bool = False
    # Tier (c): a final batched adjudication over ONLY the pairs tier (b) left
    # "unsure". Its matches are always reported as LOW confidence / review
    # required.
    qa_checklist_adjudicate_enabled: bool = False
    # Deterministic remediation: when ON, the bounded critic loop's stop
    # condition becomes "every checklist item is traced" instead of "the LLM
    # critic ran out of patience". Requires qa_atomic_checklist_enabled.
    qa_checklist_remediation_enabled: bool = False
    # Embedding-cosine bands. score >= high -> HIGH-confidence match;
    # low <= score < high -> the ambiguous band handed to tiers (b)/(c);
    # score < low -> no match. Thresholds are dataset-dependent (TraceLLM tunes
    # 0.01..1.0 per domain against labelled ground truth); these are
    # conservative project-level defaults, NOT tuned optima. The lexical TF-IDF
    # fallback uses its own fixed constants in tools/rtm.py because its scores
    # live on a different scale.
    qa_checklist_match_high: float = 0.75
    qa_checklist_match_low: float = 0.30
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
    # Hard cap on ambiguous-band pairs handed to tiers (b)/(c) -- bounds cost and
    # latency, which the NLI traceability literature does not report.
    qa_checklist_max_pairs: int = 40

    # --- Batch 3 rule packs (opt-in, every flag default OFF) ---------------
    # Three domain rules this repo had zero handling for. All three are
    # implemented as rules about WHAT MUST APPEAR ON THE ATOMIC REQUIREMENTS
    # CHECKLIST (Batch 2), not as new pipeline stages, so the existing external
    # coverage tally enforces them and each future rule costs one prompt clause
    # instead of one stage. All three are PURE + SYNCHRONOUS: zero extra LLM
    # calls and zero network, whether on or off. Enforcement BY THE TALLY needs
    # QA_ATOMIC_CHECKLIST_ENABLED as well; with the checklist off the packs
    # degrade to prompt + advisory mode (documented, logged, not silent).
    #
    # QA_BILINGUAL_RULES — every documented EN/AR message pair (a DM##/MSG##
    # table) becomes its own mandated checklist line, gets ONE test case with
    # two steps (English locale, Arabic locale), and the two strings are carried
    # into Expected Results MECHANICALLY: the generator writes opaque
    # {{EN:DM01}} / {{AR:DM01}} tokens and tools/bilingual.py substitutes the
    # values parsed from the ticket. Verbatim reproduction by an LLM
    # hallucinates, and this way the untrusted literals never enter a prompt at
    # all. Also switches tools/xlsx_generator.py to RTL-safe cells
    # (reading_order=2 + RLM/LRM bidi isolation) for Arabic-majority text, and
    # appends a templated native-speaker linguistic-validation case (the MANUAL
    # half that an automated bilingual case cannot cover).
    qa_bilingual_rules: bool = False
    # QA_ATOMICITY_RULES — the anti-bundling split rule in the generator prompt
    # ("never bundle a backend/state outcome with a UI/navigation outcome"; the
    # split boundary is the SUBSYSTEM, not a cosmetic UI toggle) plus two
    # DETERMINISTIC, FLAG-ONLY detectors. A bundled case passes on the visible
    # half while the hidden half is silently broken.
    qa_atomicity_rules: bool = False
    # QA_STANDING_RULES — content-triggered mandates. A genuine API mention
    # forces status-code / request-design / response-structure lines (plus error
    # handling when a failure flow is named); any user-facing screen forces one
    # baseline UI build-quality line. With no documented contract the cases are
    # written against standard REST convention and labelled ASSUMED
    # mechanically. The API trigger is two-tiered: circumstantial words ("HTTP",
    # "JSON", "integration") need TWO distinct hits and are then reported as
    # circumstantial, because on a pure-UI ticket a single "integration with the
    # wallet screen" used to force four backend cases.
    qa_standing_rules: bool = False

    # TestRail API push (T-10). Base instance URL (e.g. https://acme.testrail.io),
    # a user email, and an API key. TESTRAIL_DRY_RUN defaults ON so a push
    # previews what WOULD be created without writing to the customer's TMS until
    # an operator explicitly sets TESTRAIL_DRY_RUN=false.
    testrail_url: str = ""
    testrail_user: str = ""
    testrail_api_key: str = ""
    testrail_dry_run: bool = True

    # Spec-document ingestion. Off by default like every other feature gate.
    # When ON, a PDF/DOCX/TXT/MD attached to a chat message is extracted to text
    # (tools/doc_ingest.py), wrapped as UNTRUSTED context, and injected into the
    # generation prompt. PDF/DOCX need the optional `spec` extra (pypdf /
    # python-docx); TXT/MD work with no extra deps.
    qa_spec_ingest_enabled: bool = False
    # When ON (and ingestion is on), the extracted spec text is ALSO written to
    # the RAG corpus as an entry_type="spec" entry for reuse / fine-tuning.
    qa_spec_rag_persist: bool = False
    # Raw upload byte cap and extracted-text char cap (mirror the image caps).
    qa_max_spec_bytes: int = 10_000_000
    qa_max_spec_chars: int = 20_000

    # Fine-tuning dataset export. Off by default. When ON, testers can type an
    # "export dataset" keyword to download the corpus (generated test cases +
    # bug reports) as a JSONL SFT dataset (tools/finetune_exporter.py).
    qa_finetune_export_enabled: bool = False
    # Output shape: "messages" (chat SFT, default) or "prompt_completion".
    qa_finetune_format: str = "messages"

    # Swagger/OpenAPI link ingestion. Off by default. When ON, a pasted spec
    # URL is fetched with the same SSRF hardening as Jira/web content,
    # condensed to a bounded endpoint summary (tools/swagger_fetcher.py), and
    # used to ground API test-case generation.
    qa_swagger_enabled: bool = False
    # Auto-build the Excel (xlsx) export the moment test-case generation
    # finishes on the MCP path (tools/mcp_handlers.handle_generate_test_cases),
    # so the reply hands the tester a ready file path -- no separate
    # qa_export_suite call and no "which format?" round trip.
    #
    # Deliberately ON by default -- the one documented exception to the
    # opt-in-defaults-OFF rule. The spreadsheet IS the deliverable a manual
    # tester came for; it is a local file write with no external side effect;
    # and defaulting in CODE (not in the generated .env) is the only way
    # already-installed users pick it up, since .env files survive updates.
    # Set QA_AUTO_EXPORT_XLSX=0 to go back to export-on-request.
    #
    # Reuses the SAME generate_test_case_xlsx code path (cell_sanitizer
    # formula-injection protection included) and never fails generation -- an
    # export error only appends a warning note.
    qa_auto_export_xlsx: bool = True

    # Directory the auto-exported .xlsx is written to, relative to the working
    # directory (the install dir the MCP server chdirs into). Defaults to the
    # gitignored data/exports so the file lands in a stable folder a
    # non-technical tester can find and re-open -- their own deliverable, never
    # auto-deleted. Set to "" for the legacy secure-temp behavior
    # (<tempdir>/qa_agents_exports/, 0600); an unusable value degrades to that
    # same temp path rather than failing the export. A plain string field: no
    # bool coercer, and it adds no internal import.
    qa_export_dir: str = "data/exports"

    # Zephyr for Jira import export (Batch 4 -- tools/zephyr_exporter.py).
    # Opt-in, OFF by default per the house rule. When ON, `zephyr` joins the
    # qa_export_suite format list and the auto-export path additionally writes a
    # Zephyr-shaped 15-column workbook plus its zfj_import_config.json field map
    # next to the Excel deliverable, so a tester imports straight into Jira
    # instead of hand-massaging the generic 11-column sheet. OFF = the export
    # surface, the format menus and the generation reply are byte-identical to
    # before, and tools/zephyr_exporter.py never runs.
    qa_zephyr_export_enabled: bool = False

    # Dry run for that export, ON by default -- the house rule for an external
    # write, and the column layout is not vendor-verified yet (operations/
    # runbook.md -> "Zephyr export pilot gate"). The IMPORT into Jira is the
    # external write, performed by the tester on our artifact, so dry run bounds
    # the artifact instead of suppressing it: the workbook holds ONE case (the
    # first multi-step one, so the multi-row layout is actually exercised), is
    # named zephyr_import_PILOT.xlsx inside a zephyr_pilot_* folder, and the
    # reply tells the tester to import it into a SANDBOX project first. Set to
    # false only after a pilot is recorded in the runbook. Both flags use the
    # lenient never-raising bool coercer like every other flag.
    qa_zephyr_dry_run: bool = True

    # Distribution / test-cases-only mode. When ON, the UI exposes ONLY the
    # test-case generation flows (feature text / Jira / web URL / Swagger link
    # / mobile screens); bug-report, exploratory-coach, Maestro and fine-tune
    # surfaces are hidden. Forced implicitly when those modules are absent
    # (the public distribution build ships without them).
    qa_dist_mode: bool = False

    # Xray (Jira test management) write-back -- mirrors the TestRail push above.
    # Xray Cloud client credentials + target project key. XRAY_DRY_RUN defaults
    # ON so a push previews what WOULD be created until an operator sets
    # XRAY_DRY_RUN=false. xray_base_url is the fixed Cloud host by default.
    xray_client_id: str = ""
    xray_client_secret: str = ""
    xray_project_key: str = ""
    xray_base_url: str = "https://xray.cloud.getxray.app"
    xray_dry_run: bool = True

    # SQLite file that persists generated suites so they survive a browser
    # refresh and stay re-exportable (T-01). Parent dirs are created on first
    # write; the store itself is never-raise (a corrupt DB degrades to
    # session-only, logged).
    qa_suite_store_path: str = "data/suites.db"

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

    # Guided, choice-driven MCP wizard (qa_wizard) + missing-parameter prompts via
    # MCP elicitation (ctx.elicit). Off by default like every other feature gate.
    # When ON, qa_wizard and the export/mobile tools ask interactive choice
    # dialogs on clients that support elicitation (Claude Code, Cursor); Claude
    # Desktop does not, so those calls transparently fall back to a markdown menu.
    # With this OFF the existing tools behave exactly as before and qa_wizard
    # still works via markdown menus.
    qa_mcp_elicit_enabled: bool = False

    # GitHub-Release startup self-update (launcher.py -> tools/updater.py).
    # Opt-in, OFF by default like every other feature gate. When ON, an operator
    # who starts the server via `python launcher.py` gets a check against the
    # configured GitHub repo's latest Release; a newer version is downloaded,
    # swapped in (operator-local state preserved), and `pip install -e .` re-run
    # before the MCP server starts. Any failure never blocks startup (see updater.py).
    qa_auto_update_enabled: bool = False
    # "owner/name" of the (private) GitHub repo to check for releases. Empty
    # disables the check even when the flag above is on.
    qa_update_repo: str = ""
    # GitHub token for the Releases API + zipball download. REQUIRED for a
    # private repo (fine-grained PAT, read-only Contents scope). Sent only as an
    # Authorization: Bearer header, never logged. .env only.
    github_token: str = ""
    # Bounded network timeout (seconds) for the release check + download.
    qa_update_timeout: int = 10
    # Integrity self-heal + read-only code lock (distribution installs). When ON
    # (or forced by the dist launcher), startup verifies every MANIFEST.sha256
    # entry, re-downloads locally-modified files from the current release, and
    # chmods code files read-only. OFF by default for developer checkouts.
    qa_code_lock_enabled: bool = False

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
    # default) AND neither opt-out is set. Constitution note: feature gates
    # default OFF, but telemetry follows the CLI industry standard (opt-out)
    # - it stays inert in the private checkout (no key) and is turned ON only
    # by the distribution build, with README disclosure + two opt-outs.
    qa_telemetry_disabled: bool = False
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
        "qa_web_search_enabled",
        "qa_rag_enabled",
        "qa_token_meter_enabled",
        "qa_coverage_regen_enabled",
        "qa_coverage_regen_merge_calls",
        "qa_ac_anchoring_enforce",
        "qa_cot_reasoning_enabled",
        "qa_mutation_eval_enabled",
        "qa_feature_analysis_enabled",
        "qa_test_plan_artifacts",
        "qa_llm_risk_scoring",
        "qa_test_data_strategy",
        "qa_quality_reminder_upfront",
        "qa_surgical_quality_retry",
        "testrail_dry_run",
        "jira_fetch_comments",
        "qa_comment_reconcile_enabled",
        "jira_fetch_images",
        "jira_fetch_parent",
        "qa_mobile_capture",
        "qa_maestro_enabled",
        "qa_maestro_dry_run",
        "qa_maestro_heal_enabled",
        "qa_maestro_explore_enabled",
        "qa_maestro_translate_enabled",
        "qa_spec_ingest_enabled",
        "qa_spec_rag_persist",
        "qa_finetune_export_enabled",
        "qa_swagger_enabled",
        "qa_auto_export_xlsx",
        "qa_zephyr_export_enabled",
        "qa_zephyr_dry_run",
        "qa_dist_mode",
        "qa_mcp_enabled",
        "qa_mcp_elicit_enabled",
        "qa_jira_preflight",
        "qa_auto_update_enabled",
        "qa_code_lock_enabled",
        "qa_update_require_signature",
        "qa_telemetry_disabled",
        "xray_dry_run",
        "qa_llm_strict_host",
        "qa_web_run_enabled",
        "qa_web_run_dry_run",
        "qa_bilingual_rules",
        "qa_atomicity_rules",
        "qa_standing_rules",
        "qa_semantic_dedup_enabled",
        "qa_atomic_checklist_enabled",
        "qa_checklist_nli_enabled",
        "qa_checklist_adjudicate_enabled",
        "qa_checklist_remediation_enabled",
        "qa_model_tiering_enabled",
        "qa_prompt_cache_enabled",
        "qa_structured_json_enabled",
        "qa_structured_json_strict",
        "qa_terse_category_output_enabled",
        "qa_max_tokens_tiering_enabled",
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

    @field_validator(
        "qa_model_tier_coverage_gaps",
        "qa_model_tier_maestro_translate",
        mode="before",
    )
    @classmethod
    def _coerce_model_tier(cls, v: object, info) -> str:
        """Lenient enum-like coercer for the per-site model-tier overrides.

        Mirrors _coerce_jira_int's shape: an unrecognised value is logged at
        WARNING and replaced with "default" (follow qa_model_tiering_enabled)
        rather than raising or wedging the setting into a value
        resolve_tiered_model doesn't understand.
        """
        token = str(v).strip().lower() if v is not None else "default"
        if token not in ("default", "haiku", "sonnet"):
            logger.warning(
                "Invalid %s=%r -- expected default/haiku/sonnet; using default",
                info.field_name.upper(),
                v,
            )
            return "default"
        return token

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
        "qa_checklist_match_low",
        "qa_checklist_min_granularity",
        mode="before",
    )
    @classmethod
    def _coerce_checklist_float(cls, v: object, info) -> float:
        """Lenient, never-raising float coercer for the Batch-2 checklist bands.

        Mirrors _coerce_jira_int: an unparseable value is logged and replaced
        with the field's declared default rather than raising.

        All three fields are similarity / quality SCORES, so the parsed value is
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
        "qa_comment_reconcile_field_threshold",
        "qa_comment_reconcile_dedup_threshold",
        mode="before",
    )
    @classmethod
    def _coerce_reconcile_threshold(cls, v: object, info) -> float:
        """Lenient, never-raising float coercion for the reconciler thresholds."""
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
        "qa_comment_reconcile_max_comments",
        "qa_comment_reconcile_max_amendments",
        "qa_comment_reconcile_max_chars",
        "jira_max_images",
        "jira_max_image_bytes",
        "jira_max_parent_chars",
        "qa_max_chat_images",
        "qa_max_chat_image_bytes",
        "qa_device_command_timeout",
        "qa_device_screenshot_timeout",
        "qa_maestro_run_timeout",
        "qa_maestro_heal_max_attempts",
        "qa_maestro_explore_max_steps",
        "qa_maestro_explore_step_timeout",
        "qa_maestro_translate_concurrency",
        "qa_coverage_regen_max_rounds",
        "qa_max_spec_bytes",
        "qa_max_spec_chars",
        "qa_llm_timeout_s",
        "qa_rag_recency_half_life_days",
        "qa_rag_max_entries",
        "qa_update_timeout",
        "qa_web_run_max_cases",
        "qa_web_run_vision_budget",
        "qa_web_run_timeout_s",
        "qa_checklist_max_items",
        "qa_checklist_max_prompt_chars",
        "qa_checklist_max_pairs",
        "qa_prompt_cache_min_tokens",
        "qa_prompt_cache_warm_max_tokens",
        "qa_llm_max_tokens_category",
        "qa_llm_max_tokens_critic",
        "qa_llm_max_tokens_rewrite",
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

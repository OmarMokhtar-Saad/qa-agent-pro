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
        # NB: qa_category_stall_s is intentionally ABSENT -- 0 is its documented
        # kill-switch, and membership here would rewrite it to the default.
        "qa_category_stall_strikes",
        "qa_host_dedup_max_groups",
        "qa_host_dedup_max_group_size",
        "qa_host_coverage_max_items",
        "qa_host_coverage_max_tc_per_item",
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

    # Liveness (stall) detection for the streaming cli/cursor backends. The
    # per-category deadline above cannot tell "slow but streaming" from "wedged",
    # so it killed categories that were actively producing output (measured
    # 2026-07-28 on a real ticket: 6 of 8 categories dropped, all mid-stream).
    # These bound SILENCE instead of elapsed time: llm._ask_json_cli waits this
    # long for the next token, and gives up only after this many consecutive
    # idle windows.
    #
    # ON by default -- unlike a new capability, this repairs a default-path
    # defect, and it also aborts a genuinely dead subprocess SOONER than the
    # deadline did. 0 disables detection entirely (the kill-switch), which is why
    # qa_category_stall_s is deliberately kept OUT of _POSITIVE_INT_FIELDS;
    # llm._resolve_stall_policy clamps negatives at the point of use.
    qa_category_stall_s: int = 120
    qa_category_stall_strikes: int = 3

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

    # Test-generation orchestration mode (QA_GENERATION_MODE, default "server"
    # per the defaults-OFF rule). "server" -> the MCP server runs the 8-category
    # LLM fan-out through its own backend (byte-identical to before host mode).
    # "host" -> the server returns a grounded prompt for the tester's OWN chat
    # model (any MCP host) to generate, then validates the submitted JSON — no
    # server-side API key/CLI/quota needed. "auto" -> reuse llm's host/backend
    # detection (server when the host editor's own backend is usable, host
    # otherwise), which also turns a hard LLMBackendUnavailableError into
    # graceful degradation. Resolved by llm.resolve_generation_mode().
    # 2026-08-01: HARDCODED to "host" by _coerce_generation_mode below --
    # QA_GENERATION_MODE is no longer read from .env at all. Test-case
    # generation must never run through a server-side CLI/API/cursor backend;
    # the 8-category fan-out always runs on the tester's own chat model. This
    # default is now purely documentation of the effective value, not a
    # user-configurable one. Does NOT affect the other agents (bug reporter,
    # exploratory coach, Feature Analysis, mobile healing, RAG, RTM), which
    # call llm.py directly and never call resolve_generation_mode().
    qa_generation_mode: str = "host"

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

    # Still read: used ONLY to recognise a self-hosted Jira on a custom domain
    # (tickets.example.com) as a ticket URL rather than a generic web page.
    jira_base_url: str = ""
    # DEPRECATED 2026-08-01, retained as inert fields. The REST/Basic-Auth Jira
    # path was removed in favour of the calling agent's own Atlassian MCP
    # connection (OAuth 2.1, Jira Cloud), so nothing reads these any more. They
    # are kept so an existing .env carrying JIRA_EMAIL / JIRA_API_TOKEN still
    # loads cleanly instead of tripping validation on upgrade -- and, because
    # nothing reads them, a stale token cannot silently be used either.
    jira_api_token: str = ""
    jira_email: str = ""
    # Tool-name prefix the CALLING agent uses for its Atlassian MCP tools.
    # Claude Code / Desktop expose them as `mcp__atlassian__getJiraIssue`;
    # other clients namespace differently, so the directive this server returns
    # must be adjustable rather than hardcoded. Empty falls back to the Claude
    # form. Not a feature flag -- it changes wording, never behaviour.
    qa_jira_mcp_tool_prefix: str = "mcp__atlassian__"
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

    # Search the OTHER custom fields for one whose value reads like requirements
    # when the configured `jira_ac_field` does not. OFF by default and staying
    # that way until an operator asks: adopting the wrong field is exactly the
    # failure that made a date field's timestamp the only "acceptance criterion"
    # on a real run. `qa-doctor` discloses what was resolved either way, so
    # a mis-configured field is visible without this being on.
    qa_jira_ac_field_discovery: bool = False

    # Kill switch for the deterministic grounding/consistency advisories on the
    # finalize summary (undefined option values, requirements no case mentions,
    # defects in the ticket itself, unfalsifiable oracles, contradictory state
    # assumptions). Default ON is a DELIBERATE exception to the defaults-OFF
    # rule, documented in docs/FEATURE_FLAGS.md: these run no model, mutate no
    # case, and render "" when they find nothing, so an unaffected run's summary
    # is byte-identical -- the same reasoning that leaves quality_warning_section
    # unflagged. The switch exists because a code review noted there was no way
    # to turn them off if a heuristic ever misfires on a particular team's style.
    qa_grounding_advisories_enabled: bool = True

    # Ask the tester's own chat model to classify every generated case as
    # entailed / ungrounded / unspecified against the ticket, and route the
    # ungrounded ones onto their own export sheet instead of the executable
    # suite. This is the ONE judgement none of the deterministic checks can make:
    # they are all lexical, so a fabricated-but-fluent case ("a refund status
    # appears") passes every one of them. Zero extra round trips -- the
    # instruction rides the existing prepare payload and the verdicts ride the
    # existing submission, exactly like the duplicate review.
    #
    # OFF by default, and genuinely opt-in rather than a documented exception:
    # unlike the deterministic advisories this changes what the host is ASKED to
    # do, spends the tester's tokens on one judgement per case, and its output is
    # model-authored. tools/grounding_verdicts.py bounds what that output may do
    # -- ids matched against the suite's own, verdicts enum-gated, notes capped,
    # a 40% proportional ceiling mirroring screen_duplicate_groups, and cases
    # MOVED rather than deleted -- so the worst a hostile verdict list achieves
    # is reviewer noise.
    qa_host_grounding_review_enabled: bool = False
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

    # --- Jira image gate (QA_IMAGE_GATE_ENABLED) -- default ON. ------------
    # A DISCLOSURE, not a feature. Jira is read through the calling agent's own
    # Atlassian MCP connection, which returns attachment METADATA and never the
    # image bytes (tools/jira_mcp makes no outbound HTTP request, by hard rule),
    # so a ticket whose requirements live in mockups silently produced a suite
    # written from ticket TEXT alone. ON, qa_prepare_test_cases says so BEFORE
    # the fetch (beat 1, collecting a source plan: attach / capture / both /
    # text-only) and again -- naming the ticket's OWN screens -- once the
    # fetched ticket reveals images the chosen plan did not supply (beat 2).
    # Default ON is the SAME deliberate exception the honest-disclosure family
    # already carries (JIRA_FETCH_IMAGES, QA_GROUNDING_ADVISORIES_ENABLED,
    # QA_AMBIGUITY_GATE_SEVERITY on the MCP path): defaulting a disclosure OFF
    # means a fresh install keeps shipping exactly the suite the gate exists to
    # flag. false restores the pre-gate flow exactly -- the post-hoc "could not
    # read images" notice on the finished payload, and nothing else.
    qa_image_gate_enabled: bool = True
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

    # Per-phase breakdown on the meter line (opt-in, default OFF). When ON the
    # summary gains a second "By phase" line splitting the run's tokens across
    # generation / critic / rewrite / other. Purely presentational: it never
    # changes what is generated, only what is reported.
    qa_token_meter_detail_enabled: bool = False

    # $ cost estimate on the meter line (opt-in, default OFF). Priced ONLY from
    # calls whose REAL API usage was captured (api backend); a char-estimated
    # call is reported as unpriced rather than silently priced from a guess.
    qa_token_meter_cost_enabled: bool = False

    # Approximate per-1M-token prices for the two model tiers this codebase
    # actually configures (qa_llm_model / qa_classifier_model). These are
    # APPROXIMATE published rates and drift; override via .env rather than
    # treating them as authoritative. The cost line is always labelled an
    # estimate and never drives generation behaviour, so a stale default
    # degrades to a slightly-off dollar figure, nothing more. Coerced by
    # _coerce_token_price (never raises; negatives clamp to 0.0).
    qa_token_price_generation_input_per_1m: float = 3.0
    qa_token_price_generation_output_per_1m: float = 15.0
    qa_token_price_classifier_input_per_1m: float = 1.0
    qa_token_price_classifier_output_per_1m: float = 5.0
    # Anthropic bills cache reads/writes as a fraction/multiple of the SAME
    # tier's input rate, so these scale that rate rather than adding two more
    # absolute-rate fields per tier.
    qa_token_price_cache_read_discount: float = 0.1
    qa_token_price_cache_write_multiplier: float = 1.25

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

    # Ambiguity-gate verdict cache TTL, in seconds. 0 = OFF (default, today's
    # behaviour: every prepare re-classifies). MEASURED 55.8s for ONE small
    # classification on the `cli` backend on the 2026-07-30 run -- ~56% of the
    # whole server-controlled cost of that session -- and a plain re-prepare of
    # the SAME ticket pays it again in full. The cache is process-local, bounded
    # (32 entries, FIFO) and keyed on the classified text + gate severity +
    # classifier model, so changing the gate or the model MISSES.
    # NB deliberately NOT in _POSITIVE_INT_FIELDS: 0 is its documented
    # kill-switch, exactly like qa_category_stall_s, and membership there would
    # rewrite it to the default.
    # LOAD-BEARING: a `degraded` verdict is NEVER cached (see
    # tools/mcp_handlers._ambiguity_cache_put). Degraded means "could not
    # classify" -- the SHYJ-7154 fail-safe -- so caching it would freeze a
    # transient backend outage into a sticky CLARIFY.
    qa_ambiguity_cache_ttl_s: int = 0

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

    # Retype the "Edge Cases" fan-out category from Exploratory to Functional
    # (opt-in, default OFF). CATEGORIES[3] tells the model the preferred `type`
    # for that category is "Exploratory", so on the 2026-07-30 run all 8
    # fully-SCRIPTED edge cases exported as Exploratory and the XLSX Summary
    # reported "Exploratory 8 / Performance 0" -- test-type metrics describing
    # unscripted charter testing for a suite that contains none. This is a PROMPT
    # change, so it is flag-gated for two reasons: the house rule, and because
    # tests/test_server_mode_equivalence.py's golden fixtures record the 8
    # category prompts VERBATIM (`should be: Exploratory` included). OFF,
    # agents.test_scenario_agent.effective_categories() returns the CATEGORIES
    # object itself and every prompt byte is unchanged.
    qa_edge_cases_functional_type: bool = False

    # Merge a QUALIFIER-PREFIXED module label onto the bare label it qualifies
    # (opt-in, default OFF). tools/quality_checks.normalize_module_names has always
    # merged CASING/whitespace variants, but it buckets on a casefolded key, so
    # "Sehhaty Store - Cancel Order" and "Cancel Order" are different keys and never
    # merged: a real 2026-08-03 run shipped ONE feature split 12 / 86 across exactly
    # those two labels, fragmenting every group-by-module view (Jira, TestRail, the
    # XLSX pivot). ON, a second pass merges the qualified label into the bare one.
    #
    # Flag-gated because a WRONG merge silently destroys a real distinction, which
    # is worse than the split it fixes. The rule merges ONLY on TAIL containment and
    # REFUSES head containment -- "Store Wallet - Top Up" is a sub-module of "Store
    # Wallet", not a variant of it -- and refuses a tail claimed by more than one
    # qualifier ("Admin - Login" + "User - Login"). Read at the CALL SITE via
    # getattr, so an install whose .env predates this field is byte-identical.
    # See tools/quality_checks._qualifier_prefix_merges for the full rationale.
    qa_module_prefix_normalize_enabled: bool = False

    # Extract acceptance criteria from a USE-CASE TABLE description.
    # Default **ON** since 2026-08-03, user-approved -- a deliberate exception to
    # the defaults-OFF rule, in the same family as JIRA_FETCH_PARENT /
    # JIRA_FETCH_COMMENTS / JIRA_FETCH_SIBLING_STORIES.
    #
    # The AC fallback in tools/jira_mcp only understands an "Acceptance Criteria"
    # heading followed by a block. A whole ticket family writes its requirements as
    # a markdown UC table instead -- rows labelled Basic Flow / Alternative Flow /
    # Business Rules / Post-condition -- with no such heading anywhere.
    #
    # WHY THE DEFAULT FLIPPED. This shipped OFF on the reasoning that it adds
    # ticket text to the generation prompt, and a mis-parse could introduce
    # requirements the ticket never stated. That weighed the risk against the wrong
    # baseline. With this OFF the run does not get NO acceptance criteria -- the
    # host's AC_JOB SYNTHESIZES them, and the first production v1.34.0 run
    # finalized with SIX model-invented criteria and a "6/6 traced, all covered"
    # RTM built on them. Measured on that same ticket, ON yields FOUR criteria read
    # out of the ticket's own table. So the real choice is not "extra text vs no
    # extra text", it is "criteria read from the ticket vs criteria invented by a
    # model", and reading them is plainly safer.
    #
    # The mis-parse risk is bounded and disclosed rather than hidden: the extractor
    # takes only requirement-bearing rows (context rows like Description / Actor /
    # Pre-condition are skipped), caps at 12 rows x 600 chars, an explicit
    # "Acceptance Criteria" heading still wins, and anything it produces still
    # flows through the untrusted-text path. Set to false to restore the previous
    # behaviour -- note that this means going back to model-synthesized criteria on
    # this ticket family, not to none.
    qa_jira_uc_table_ac_enabled: bool = True

    # Re-register this server in editor MCP configs on startup (opt-in, OFF).
    # Registration otherwise happens exactly ONCE, at install: connect.sh skips a
    # client whose config dir does not exist yet, install.sh refuses to re-run
    # without QA_FORCE, and the launcher never called connect.sh -- so an editor
    # installed AFTER qa-agent-pro is never picked up and the tester has no way to
    # know connect.sh needs re-running.
    #
    # OFF by default, deliberately, even though that limits reach: this WRITES to
    # files outside the install dir (~/.cursor/mcp.json, Claude Desktop's config),
    # and a server that inserts itself into other editors' configs whenever it
    # starts is a shape worth requiring consent for. The write is bounded to
    # INSERTING one absent key -- an existing entry is never rewritten -- and is
    # atomic + locked (tools/client_registry). Note the limit this cannot escape:
    # if NO client is registered, nothing launches the server, so a startup pass
    # can never bootstrap the very first client. Running connect.sh remains the
    # answer for that case, and qa-doctor points at it.
    qa_auto_register_clients: bool = False

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
    qa_register_atlassian_mcp: bool = True

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

    # 2026-08-04 (user-approved): qa-doctor repairs values in the install's
    # own .env that are still EXACTLY a default this project shipped and later
    # superseded (tools/env_heal.HEAL_RULES). ON by default, and a deliberate
    # exception to the defaults-OFF rule for the same reason as
    # qa_auto_export_xlsx: a default-OFF switch would never be found by the
    # non-technical testers it exists for, and updater.migrate_env cannot do this
    # job -- it only APPENDS missing keys and never rewrites a line, so a stale
    # 1500 or an inherited `false` outlives every release. A value the operator
    # chose is never touched, a commented-out key is treated as a deliberate
    # opt-out, and the file is backed up before any write.
    # Set QA_ENV_SELFHEAL_ENABLED=false to disable.
    qa_env_selfheal_enabled: bool = True

    # Export the computed risk SCORE into the XLSX Notes column (opt-in,
    # default OFF). tools/risk_scorer.py scores EVERY suite and the sheet's row
    # order IS the risk order (TC-001 = highest risk), but risk_label /
    # risk_score were never exported -- while the Notes column was empty in
    # 65/65 rows of the 2026-07-30 run, because it only ever carried a Batch-3
    # rule-pack note and the rule packs are off in that deployment. When ON, a
    # case with NO rule-pack note gets "Risk 78"; a rule-pack note ALWAYS wins,
    # so nothing is ever displaced. OFF = every exported workbook is
    # byte-identical to today. 2026-08-04: the risk LABEL left that cell -- it
    # duplicated the Priority column and contradicted it on 10/97 rows of that
    # day's run; only the SCORE, the sheet's row-order key, is written now.
    qa_xlsx_risk_notes: bool = False

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

    # Host-mode pending-generation store (tools/prep_store.py). A prep record
    # (grounded prompt + checklist + category specs + bounds + provenance) is
    # persisted between qa_prepare_test_cases and qa_submit_suite. TTL after
    # which a prep record is expired on read, and the max serialized payload
    # size accepted (a host cannot wedge the store with a pathological
    # submission). Both never-raise-coerced as positive ints.
    qa_prep_ttl_s: int = 3600
    qa_prep_max_bytes: int = 4000000

    # --- Inline Feature Analysis report on a HOST submit (opt-in, default OFF) -
    # QA_FEATURE_ANALYSIS_ENABLED does TWO unrelated things: it registers the
    # standalone qa_feature_analysis tool (mcp_server.py) AND makes every
    # _finalize_generation run agents.feature_analysis.analyze_feature -- one
    # server-side LLM call. On the host path that second half straight-up
    # contradicts host mode's premise (no key, no backend, no quota), and it is
    # expensive: MEASURED 42.0s on the 2026-07-30 SHYJ-5645 run (69s on an
    # earlier one) against 0.02s for the whole deterministic finalize of 65
    # cases -- the report was ~99.95% of that step. This flag SPLITS them: the
    # tool stays available on demand, the inline report on a HOST submit runs
    # only if an operator asks for it. Server mode is untouched --
    # _finalize_generation's feature_report_enabled parameter defaults True and
    # only the host submit call site passes this flag.
    qa_host_feature_report_enabled: bool = False

    # --- Host-side ambiguity preflight (opt-in, default OFF) ---------------
    # WHY: QA_GENERATION_MODE=host only boomerangs the 8-category fan-out.
    # The SHYJ-7154 ambiguity gate still runs server-side via llm.ask_json ->
    # QA_LLM_BACKEND (usually cli / claude subprocess). MCP cannot call the
    # Cursor chat model as an llm.py backend. Tonight's 2026-07-30 evening
    # run hit a Claude CLI session limit, returned degraded, and told the
    # tester the ticket was "under-specified" even though DF01/DF02 were
    # present. When this flag is ON and generation mode resolves to host,
    # prepare SKIPS the server classifier and returns an ambiguity_job for
    # the chat to run first (prepare-side preflight -- NOT the blocked F12
    # submit-side ambiguities GO/NO-GO). Server mode is untouched.
    qa_host_ambiguity_review_enabled: bool = (
        True  # 2026-08-01: hardcoded ON, see _force_host_boomerang_on
    )

    # --- Host-derived acceptance criteria (opt-in, default OFF) -------------
    # WHY: rtm.generate_acs is an UNCONDITIONAL server-side llm.ask_json. It
    # fires on every prepare whose ticket carried no parsed acceptance criteria
    # (agents/test_scenario_agent._need_acs) -- common for sub-tasks and bugs --
    # and it has no off switch, so a keyless / quota-dead host-mode install
    # cannot get past it. AC synthesis is PREPARE-side (its output feeds
    # rtm_hint, the RTM and the atomic checklist), so it cannot be deferred to
    # submit: when this flag is ON and generation mode resolves to host, prepare
    # SKIPS the call and ships an `acceptance_criteria_job` in the payload
    # instead. The tester's own chat model derives the criteria, tags
    # requirement_id with them, and returns them beside its suite; submit
    # validates that field as UNTRUSTED input and labels it MODEL-DERIVED, never
    # ticket-sourced. Server mode is untouched (_prepare_generation's
    # synthesize_acs parameter defaults True and only host prepare passes False).
    qa_host_ac_review_enabled: bool = (
        True  # 2026-08-01: hardcoded ON, see _force_host_boomerang_on
    )

    # --- Refuse a host submit with no verified ambiguity preflight (OFF) ----
    # QA_HOST_AMBIGUITY_REVIEW_ENABLED hands the SHYJ-7154 pre-pass to the host,
    # which also removes the server's only evidence that the check happened. The
    # job now asks for an `ambiguity_result` back and the submit reply ALWAYS
    # discloses a missing or `high` verdict. This flag turns that disclosure into
    # a REFUSAL. Default OFF because refusing throws away a generation the tester
    # already paid for, and the disclosure is the honest-by-default behaviour this
    # codebase prefers; an operator who genuinely relies on the gate turns it on.
    # Inert unless QA_HOST_AMBIGUITY_REVIEW_ENABLED actually shipped the job (it
    # is keyed off the prep's meta stamp, not off the flag, so a mid-flow flip
    # cannot change an in-flight prep).
    qa_host_ambiguity_require_result: bool = False

    # --- Host-derived image descriptions (opt-in, default OFF) -------------
    # WHY: the last two server-side LLM calls on the host path are BOTH
    # vision-only -- tools/ui_extractor's Tier-3 ask_vision (a rendered
    # screenshot of a non-Jira page) and tools/image_description.describe_images
    # (screenshots / mockups the tester attached to the chat). llm.ask_vision is
    # api-backend only, so on QA_LLM_BACKEND=cli/cursor both already no-op with
    # their "Error: ..." sentinel: image grounding is silently LOST there today,
    # not saved. When this flag is ON and generation mode resolves to host, the
    # server makes NEITHER call and instead forwards the RAW bytes to the
    # tester's OWN multimodal chat model as MCP image content -- the same
    # mechanism Jira ticket images already use (PreparePayloadResult.images) --
    # together with an `image_description_job` in the payload. The host describes
    # them in-chat (no llm.py, no backend, no key, no quota) and returns an
    # OPTIONAL top-level `image_descriptions` array, which submit validates as
    # UNTRUSTED input exactly like `acceptance_criteria`. It ALSO lifts the
    # attached_images host-routing suppression in tools/mcp_handlers, which only
    # existed because those attachments were consumable server-side alone.
    # Server mode is untouched: every branch is gated on the flag AND on
    # llm.resolve_generation_mode() == "host".
    qa_host_image_description_enabled: bool = (
        True  # 2026-08-01: hardcoded ON, see _force_host_boomerang_on
    )

    # --- Host-reviewed duplicate review (Piece 1; opt-in, default OFF) -----
    # qa_semantic_dedup_enabled needs qa_embeddings_backend, and the only KEYLESS
    # embeddings backend is "local" (sentence-transformers, ~2 GB of torch), so on
    # a keyless host-mode deployment neither runs and ONLY byte-identical
    # duplicates are collapsed (_dedupe_cases keys on the content hash). A real
    # 2026-07-29 run submitted 8 categories x 8 cases and kept all 64 -- the
    # categories are generated in PARALLEL, blind to each other, which is exactly
    # how cross-category near-duplicates appear.
    # When this is ON, qa_prepare_test_cases asks the tester's OWN chat model --
    # already in the loop, already holding the merged set -- to return an OPTIONAL
    # top-level `duplicate_groups` alongside the suite, and qa_submit_suite
    # validates and acts on it in pure Python. Zero extra round trips (the field
    # rides the existing submission), zero extra server-side LLM calls, no API key.
    # 2026-08-03: default flipped OFF -> ON. Report-only, and the evidence is two
    # runs: the 2026-07-29 run above kept all 64 of 8x8 submitted cases, and the
    # 2026-08-03 SHYJ-5645 run shipped 18 duplicates across 12 clusters. With this
    # OFF the only thing that collapses anything on a keyless install is an exact
    # content hash (qa_semantic_dedup_enabled is also OFF and additionally needs an
    # embeddings backend), so step 5 of the host instructions was a politely-worded
    # request that nothing verified. ON, the host is asked for `duplicate_groups`
    # and the server validates what comes back in pure Python. REMOVAL is a
    # separate decision and stays OFF -- see qa_host_dedup_apply below, whose
    # reasoning (host output is attacker-influenceable through the _GUARD-wrapped
    # ticket text) is unchanged by this flip.
    qa_host_dedup_review_enabled: bool = True
    # Sub-flag: actually REMOVE the non-keeper members of each reported group.
    # Default OFF, and deliberately ASYMMETRIC with qa_semantic_dedup_enabled
    # (which does remove): that path drops on a NUMERIC cosine >=
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

    # --- Host-reviewed requirement coverage (Piece 2; opt-in, default OFF) ---
    # MOOT WITHOUT QA_ATOMIC_CHECKLIST_ENABLED: with no atomic requirements
    # checklist there is nothing to match test cases against, so this flag is a
    # safe no-op (agents/host_mode._coverage_instruction returns "" when the prep
    # presented no checklist item, and extract_requirement_matches has no known
    # item ids to validate against and ignores the field).
    # WHY IT EXISTS. tools/rtm.match_checklist is TIERED and the tiers are NOT
    # alternatives: tier (a), the similarity matrix, is the GATE, and the optional
    # entailment/adjudication tiers only RE-JUDGE the shortlist tier (a) already
    # surfaced. With no qa_embeddings_backend tier (a) degrades to a TF-IDF lexical
    # matrix, so the shortlist is built from word overlap -- and requirement
    # <-> test-case matching is exactly the high-paraphrase-distance case where
    # word overlap is weakest. When this is ON, the tester's OWN chat model --
    # already in the loop, already holding the merged 8-category suite and the
    # checklist block -- returns an OPTIONAL top-level `requirement_matches`
    # mapping {CL id: [tc_id, ...]} alongside the suite it already submits, and
    # qa_submit_suite reports it in pure Python. Zero extra round trips, zero
    # server-side LLM calls, no API key.
    # IT IS MODEL JUDGEMENT, NOT MEASUREMENT, and is kept structurally apart from
    # the deterministic measurement: it publishes NO percentage, is never averaged
    # or merged into ChecklistCoverage, never reaches the XLSX or the suite_store,
    # and never drives the qa_checklist_remediation_enabled gap loop (a lexical
    # measurement is FORBIDDEN to drive that loop, so letting an uncalibrated
    # self-assessment do it would invert the honesty rule). All three honesty rules
    # stand unchanged: the deterministic percentage stays suppressed while the
    # matcher is degraded, NOT-PRESENTED requirements stay excluded from every
    # count, and the textual-coverage boundary is restated in the section.
    qa_host_coverage_review_enabled: bool = False
    # Caps on the UNTRUSTED field's SHAPE: how many mapping entries are read and
    # how many tc_ids one requirement may name. Never-raise-coerced as positive
    # ints and additionally hard-capped in agents/host_mode.py, whose constant wins
    # when it is smaller. These are shape caps only -- unlike the duplicate review
    # above there is NO destructive path to bound: the field is report-only by
    # construction, and its one coverage-affecting direction (self-reported gaps)
    # is bounded by the "no claims at all => UNUSABLE review" rule in host_mode.
    qa_host_coverage_max_items: int = 500
    qa_host_coverage_max_tc_per_item: int = 12

    # --- Host-mode parallel chat fan-out (opt-in, default OFF) -----------------
    # When ON, qa_prepare_test_cases adds an orchestration contract so the PARENT
    # chat can spawn one same-session worker per category (Cursor Task / equivalent).
    # MCP cannot invoke host Task tools; this flag only changes the prepare payload,
    # instructions, status tool, and the empty-suite finalize completeness gate.
    # Primary finalize (Path A, crash-safe -- 2026-07-31 incident): qa_submit_category
    # x N as each worker returns, then empty qa_submit_suite (+ optional
    # acceptance_criteria/ambiguity_result sidecar), gated until all expected
    # categories are staged. Fallback (Path B): merge in parent + ONE full
    # qa_submit_suite -- keeps the dedup/coverage review (which needs the merged
    # suite's global tc_ids) but is lost wholesale if the chat dies before that
    # single call, which is exactly how the first live run died.
    # Flag OFF => prepare payload / instructions byte-identical to today.
    qa_host_parallel_fanout_enabled: bool = False

    # --- Host-mode duplicate-prepare guard (default ON as of 2026-08-01) ------
    # WHY: host mode's premise is that the tester's own chat model drives
    # generation end to end, and qa_prepare_test_cases is stateless -- nothing
    # stops the SAME chat turn from silently re-running an entire ticket a
    # second time (fresh Jira fetch, fresh 8-category fan-out, fresh submit)
    # instead of asking the tester first or resubmitting under the existing
    # prep_id. Observed 2026-08-01, TWICE: one "create test cases for
    # SHYJ-5645" request produced two independent finalized suites four
    # minutes apart (68 then 77 cases) even though the first finalize
    # reported zero coverage or quality gaps -- and the SAME ticket was
    # regenerated from scratch again in a later session with the guard
    # still off, with no warning that a finished suite already existed.
    # Nothing server-side ever asked for a redo either time -- this is the
    # host model's own unannounced decision, and a guard against an
    # already-observed failure mode should not require an operator to
    # discover and enable it (same reasoning as the prep crash-safety flags
    # above). When ON, qa_prepare_test_cases checks
    # suite_store.list_recent_suites for a suite whose source_url matches
    # this request's source_url within QA_HOST_DUPLICATE_PREP_WINDOW_S and,
    # if found, returns a clarify notice instead of proceeding -- the host
    # must either pass proceed_anyway=true (an explicit, visible decision,
    # already a supported parameter) or use the existing suite instead of
    # starting over. Deliberately keyed on source_url only (a
    # Jira/issue/web/Swagger URL): free-text feature descriptions have no
    # stable identity to dedupe against and are never flagged. Best-effort
    # and fail-open: any suite_store error is treated as "no duplicate
    # found", since this is a UX guard, not a correctness gate. An operator
    # with a real reason can still set this to false in .env.
    qa_host_duplicate_prep_guard_enabled: bool = True
    qa_host_duplicate_prep_window_s: int = 1800

    # --- Host-boomerang migration of ALL remaining LLM call paths (2026-08-01)
    # ------------------------------------------------------------------------
    # QA_GENERATION_MODE made test-case generation chat-only. ~30 OTHER call
    # sites still reach a server-side cli/api/cursor backend, so a keyless
    # install silently degrades (bug reports, coaching, vision grounding, the
    # ambiguity gate) and a keyed install burns a second quota for work the
    # tester's own chat model could do. qa_server_llm_enabled is the single
    # chokepoint that retires those calls; it defaults to True so introducing
    # it is a runtime NO-OP, and flipping it False is the LAST step of the
    # migration (gated on docs/LLM_MIGRATION_INVENTORY.md). OFF, llm.ask /
    # ask_vision return their existing never-raising "Error: ..." sentinel,
    # llm.ask_json raises LLMBackendUnavailableError and llm.warm_cache_prefix
    # returns False -- each path's documented contract, so every caller degrades
    # exactly as it already does when no backend is usable. Nothing invents a
    # substitute result. While any ledger row is still unmigrated, turning this
    # OFF DISABLES that feature rather than boomeranging it, so qa-doctor
    # and a one-time startup WARNING name the affected features out loud.
    qa_server_llm_enabled: bool = True
    # Per-path escape hatch for the paths that CANNOT boomerang (a completion is
    # needed mid-loop, inside a tool call): comma-separated ledger call-site ids
    # -- e.g. "maestro_healer.classify,eval_runner.judge", or "*" for all --
    # that may still call a backend directly while qa_server_llm_enabled is
    # False. Without this, one boolean conflates "retired because migrated" with
    # "disabled because unmigratable", and an operator with a working key could
    # not keep the mobile healer/explorer, web vision-verify or the eval harness
    # alive without also re-enabling every already-migrated path. Callers tag
    # themselves via llm.server_llm_scope(<id>); an UNTAGGED call is always
    # refused when the master flag is off, so the list cannot widen by accident.
    # A plain str field: any .env value is already a valid str, so no coercer is
    # needed and it can never raise at load time.
    qa_server_llm_allow: str = ""
    # Opportunistic MCP sampling (fastmcp Context.sample) for the calls that
    # cannot boomerang because the host is blocked waiting for the tool call
    # that needs the completion (maestro heal/explore, web-runner step verify,
    # eval judges, router). Default OFF: Claude Desktop/Code and Cursor do not
    # advertise the sampling capability today, so ON is a no-op there --
    # tools.host_llm.maybe_sample() returns None (never raises) and the caller
    # uses its deterministic fallback or disables the feature out loud.
    qa_host_llm_sampling_enabled: bool = False

    # --- Phase 3a of the host-boomerang migration: generation-pipeline folds --
    # Both default TRUE, deliberately, and that is NOT a defaults-OFF violation:
    # these are MIGRATION flags, not features. They add no behaviour -- they
    # REMOVE a server-side LLM call from the host path and hand the same work to
    # the tester's own chat model as one more field on the submission it was
    # already going to send (ZERO extra round trips). Same reasoning and same
    # precedent as QA_HOST_AC_REVIEW_ENABLED / QA_HOST_IMAGE_DESCRIPTION_ENABLED
    # (2026-08-01). Each is ALSO an AND with the pre-existing, default-OFF
    # feature flag it rides on, so on a default install nothing new runs at all
    # and the prepare payload stays key-identical. Setting either to false
    # restores the server-side call with no code change -- which is what makes
    # the migration's Rollback Plan true for these two rows.
    #
    # ON + QA_LLM_RISK_SCORING: the host returns a top-level `risk_scores` field
    # on its submission and tools/risk_scorer.score_with_llm is never called. The
    # deterministic score_and_sort heuristic remains the baseline and the
    # fallback for every case the host omits, and for an absent/unusable field.
    qa_host_risk_review_enabled: bool = True
    # ON + QA_TEST_PLAN_ARTIFACTS: the host returns a top-level
    # `test_plan_report` field carrying BOTH artifacts (ac_validation +
    # test_plan) and tools/test_plan_report.build_test_plan_artifacts is never
    # called. An absent or unusable field yields NO artifacts plus a disclosed
    # UNVERIFIED note -- the server does NOT fall back to making the call.
    qa_host_test_plan_review_enabled: bool = True
    # --- Residue R4: requirement decomposition on the host path -------------
    # ledger id `atomic_checklist.decompose`, the LAST row of the
    # host-boomerang migration. A MIGRATION flag, not a new feature, so it
    # defaults ON for the same reason the three above do: `migrated` may only
    # be claimed when the host path genuinely cannot reach the backend, and a
    # default of False would leave an operator with QA_ATOMIC_CHECKLIST_ENABLED
    # on still making the server-side ask_json.
    #
    # AND-ed with QA_ATOMIC_CHECKLIST_ENABLED (default OFF) AND host mode, so a
    # default install ships a key-identical prepare payload and nothing changes.
    #
    # ON + QA_ATOMIC_CHECKLIST_ENABLED + host mode: _prepare_generation is
    # called with decompose_checklist=False and makes NO decomposition call;
    # agents/host_mode.CHECKLIST_JOB (stage step_zero) asks the host to derive
    # the atomic checklist BEFORE generating and to return it as a top-level
    # `checklist_items` field. The server re-assigns every CL-NNN id, runs the
    # pure-Python audit_granularity over the result, and feeds the DETERMINISTIC
    # Pass-3 matcher unchanged. An absent or unusable field means NO checklist:
    # the server does NOT fall back to making the call it just skipped.
    #
    # Two knock-on gates are widened for this flag rather than left to break
    # silently -- tools/mcp_handlers._nli_suppress (Phase 3b) and
    # agents/host_mode._coverage_instruction -- because both are AND-ed with
    # "the prep produced checklist items", which is False at prepare time once
    # the decomposition is boomeranged. See docs/FEATURE_FLAGS.md.
    qa_host_checklist_review_enabled: bool = True

    # --- Phase 3b: the checklist NLI / adjudication tiers on the host path ----
    # A MIGRATION flag with the same default-ON rationale as the two above, and
    # the same AND with the (default-OFF) feature flags it rides on
    # (QA_CHECKLIST_NLI_ENABLED / QA_CHECKLIST_ADJUDICATE_ENABLED), so on a
    # default install nothing changes at all. It is additionally AND-ed with
    # QA_ATOMIC_CHECKLIST_ENABLED *and* with the prep actually carrying
    # checklist items, because both tiers only ever run over a checklist -- see
    # tools/mcp_handlers.py's _nli_suppress.
    #
    # ON + either tier flag + a real checklist + host generation:
    # _finalize_generation passes allow_llm_tiers=False into
    # tools.rtm.match_checklist, so tiers (b) and (c) make NO server-side
    # ask_json call on a host submit.
    #
    # There is deliberately NO host job replacing them, and that is the whole
    # finding of this sub-phase (see docs/LLM_MIGRATION_INVENTORY.md, ledger id
    # `rtm.nli_verdicts`). Both tiers exist precisely so a model OTHER than the
    # generator re-judges the deterministic shortlist -- tools/rtm.py says so in
    # source: "a DIFFERENT system prompt from the generator, so the generating
    # model still never marks its own homework". In host mode the generator IS
    # the host, so folding the tiers would (a) have the suite's own author grade
    # it and (b) feed that judgement into ChecklistCoverage, i.e. into the
    # coverage percentage, the XLSX sheets and the remediation loop -- exactly
    # what agents/host_mode.py's host-reviewed coverage block refuses to do
    # ("NOTHING IS MERGED OR AVERAGED... IT NEVER DRIVES THE GAP LOOP"). The
    # DISCLOSED host analog is QA_HOST_COVERAGE_REVIEW_ENABLED's
    # `requirement_matches`, reported as a separate, explicitly labelled tier.
    #
    # Consequence, disclosed in the submit reply, the prepare notice AND in
    # ChecklistCoverage.notes (so it survives into the exported artifact): the
    # ambiguous similarity band is reported as UNCOVERED instead of being
    # re-judged, so a run with QA_CHECKLIST_NLI_ENABLED on can show MORE gaps
    # than before. Set this to false to restore the server-side tiers with no
    # code change.
    qa_host_checklist_nli_suppress_enabled: bool = True

    # --- Phase 3c: Jira comment reconciliation on the host path ---------------
    # ledger id `comment_reconciler.candidates`. Default ON for the same reason
    # as the three flags above: this is a MIGRATION flag, not a new feature.
    # `migrated` / `disabled (disclosed)` may only be claimed when the host path
    # genuinely cannot reach the backend, so a default of False would leave an
    # operator with QA_COMMENT_RECONCILE_ENABLED=true still making the Stage 1b
    # ask_json call on every host prepare and the ledger flip would over-claim.
    # It is still an AND with the pre-existing, default-OFF
    # QA_COMMENT_RECONCILE_ENABLED, so a default install is byte-identical.
    #
    # WHY THERE IS NO HOST JOB (the load-bearing finding of Phase 3c).
    # tools/comment_reconciler.py Stage 1b is a QUARANTINED extractor: its
    # entire security value is that the model reading the raw comment thread has
    # a system prompt containing ONLY extraction instructions -- no generation
    # prompt, no test-case instructions, no tools -- so a directive injected into
    # a Jira comment has nothing privileged to target. The module docstring
    # states the resulting invariant outright: when the flag is on, tools/jira_mcp
    # STOPS appending the raw "## Comments" dump to raw_text and "the fenced
    # amendments block becomes the ONLY comment-derived input the privileged
    # generation model ever sees". In host mode the privileged generation model
    # IS the host, so ANY boomerang -- a folded HostJob or a separate
    # tools/host_llm task -- would put the raw thread into the context of the
    # model that is about to write the tests and hold the tool handles. That is
    # not a migration of the capability, it is the deletion of the defence.
    # Two further blockers, either sufficient on its own:
    #   * ORDERING. The rendered amendments block enters url_content
    #     ["amendments_context"] BEFORE _prepare_generation, i.e. it shapes the
    #     generation prompt. A HostJob return field arrives on the SUBMIT, which
    #     is far too late for a prompt-side consumer (`step_zero` is only an
    #     instruction-ordering rank inside ONE host turn, not a round trip).
    #   * A SAFETY GATE. kind="question" candidates become
    #     FLAGGED_FOR_CLARIFICATION strings that tools/mcp_handlers feeds into the
    #     QA_AMBIGUITY_GATE_SEVERITY gate, which can RETURN EARLY and refuse to
    #     prepare at all. A gate cannot be answered after the thing it gates.
    # So on the host path Stage 1b is DISABLED, not delegated: Stage 1a (the
    # pure-Python noise filter) still runs so the tester is told how many
    # comments went unreconciled, Stages 2 and 3 produce nothing, and the
    # prepare notice, the submit reply and the audit log all say so.
    #
    # Consequence, disclosed on all three surfaces: no AMENDMENTS block reaches
    # the generation prompt and no comment-derived clarification question can
    # gate the prepare, so a ticket whose current truth lives in its comment
    # thread generates from the description alone. Set this to false to restore
    # the server-side extractor with no code change.
    qa_host_comment_reconcile_suppress_enabled: bool = True

    # --- Prep crash-safety (2026-07-31 SHYJ-5645 incident; default ON as of
    # 2026-08-01) --------------------------------------------------------------
    # The first live parallel fan-out was silently lost: 8 worker packets fetched
    # via qa_get_category_job, the host window reloaded, no submit ever arrived,
    # and the prep expired after QA_PREP_TTL_S with no trace and no resume path.
    # RECURRED 2026-08-01 on this same install with these flags still off (the
    # SHYJ-5645 ticket, again) -- confirming "opt-in" was the wrong default for
    # a crash-safety guard against a failure mode already observed twice. Two
    # independent, disclosure-first mitigations, each behind its own flag,
    # BOTH now default ON (an operator with a real reason to revert can still
    # set either to false in .env):
    #
    # Sliding TTL: qa_get_category_job / qa_submit_category refresh the prep's
    # TTL clock so an ACTIVE orchestration cannot expire mid-run. Bounded by
    # qa_prep_max_lifetime_s from creation (the same anti-extension stance
    # prep_store.update_prep takes for the gap loop), so activity can never
    # extend a prep forever. OFF => the fixed created_at TTL, unchanged.
    qa_prep_sliding_ttl_enabled: bool = True
    # Hard ceiling (seconds) on a prep's TOTAL lifetime under the sliding TTL.
    # Lenient never-raise int coercion like the rest; <=0 -> the default.
    qa_prep_max_lifetime_s: int = 14400
    # Disclose unfinished preps (a fetched worker packet or >=1 staged category
    # row, not yet expired) on qa-doctor and qa_prepare_test_cases:
    # "unfinished prep <id> from HH:MM, N/8 staged, expires ~HH:MM -- resume
    # with qa_prep_status / qa_submit_category, or ignore". DISCLOSURE ONLY --
    # it never blocks a new prepare and never auto-resumes anything. The line
    # PRINTS the prep_id, which is a capability token for that prep -- turn this
    # back off (or ignore the note) anywhere the tool output is shared more
    # widely than the tester's own chat; the default-ON tradeoff is deliberate
    # because a run silently vanishing with zero trace is a worse failure mode
    # for the intended single-tester use case than a short-lived token in that
    # tester's own transcript. The "fetched packet" signal is preps.touched_at,
    # which qa_get_category_job and save_submission write whenever EITHER this
    # flag or qa_prep_sliding_ttl_enabled is on -- so a run that fetched 8
    # packets and staged nothing (the incident) is disclosed even with the
    # sliding TTL off. Writing the timestamp is free while TTL enforcement
    # stays off: prep_store._expired() reads touched_at only under
    # qa_prep_sliding_ttl_enabled.
    qa_prep_disclose_unfinished: bool = True

    # --- Phase 2 fan-out follow-ups (2026-07-31; opt-in, default OFF) -------
    # Category-qualified tc_id contract for finalize-time review sidecars.
    # Every staged category restarts its tc_ids at TC-001, so a bare
    # cross-category id is ambiguous until the merge renumbers it. ON:
    # sidecar ids may be written "<category>:<tc_id>", bare ids stay accepted
    # where unambiguous, an AMBIGUOUS bare id is refused with a loud note
    # instead of silently mapping to whichever category merged first (the
    # latent first-category-wins collision in _remap_dup_groups), and
    # requirement_matches becomes a valid sidecar field with the same
    # collision-safe remap. OFF: byte-identical to today, including the
    # documented latent collision.
    qa_qualified_tc_ids_enabled: bool = False
    # Server-assisted duplicate shortlist: when the FINAL expected category is
    # staged, qa_submit_category's reply appends lexically prescreened
    # candidate duplicate pairs (stdlib difflib over the merged cases, printed
    # with POST-MERGE GLOBAL tc_ids -- the phase-1 review settled on global
    # ids to dodge the per-category TC-001 collision trap) so the host
    # confirms a shortlist instead of re-reading the merged suite. ADVISORY
    # only; requires QA_HOST_DEDUP_REVIEW_ENABLED to matter.
    # Default ON since 2026-08-04 (documented default-OFF exception): the
    # 22:33 live finalize spent ~4.7 minutes re-reading 79 cases to remove 0;
    # the prescreen turns that into a confirm/deny of a short list.
    qa_dup_shortlist_enabled: bool = True

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
        "qa_token_meter_detail_enabled",
        "qa_token_meter_cost_enabled",
        "qa_coverage_regen_enabled",
        "qa_coverage_regen_merge_calls",
        "qa_ac_anchoring_enforce",
        "qa_cot_reasoning_enabled",
        "qa_mutation_eval_enabled",
        "qa_feature_analysis_enabled",
        "qa_test_plan_artifacts",
        "qa_llm_risk_scoring",
        "qa_test_data_strategy",
        "qa_edge_cases_functional_type",
        "qa_module_prefix_normalize_enabled",
        "qa_jira_uc_table_ac_enabled",
        "qa_auto_register_clients",
        "qa_register_atlassian_mcp",
        "qa_quality_reminder_upfront",
        "qa_surgical_quality_retry",
        "testrail_dry_run",
        "jira_fetch_comments",
        "qa_comment_reconcile_enabled",
        "jira_fetch_images",
        "jira_fetch_parent",
        "qa_jira_ac_field_discovery",
        "qa_grounding_advisories_enabled",
        "qa_host_grounding_review_enabled",
        "jira_fetch_sibling_stories",
        "qa_mobile_capture",
        "qa_image_gate_enabled",
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
        "qa_env_selfheal_enabled",
        "qa_xlsx_risk_notes",
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
        "qa_host_feature_report_enabled",
        "qa_host_ambiguity_require_result",
        "qa_host_dedup_review_enabled",
        "qa_host_dedup_apply",
        "qa_host_coverage_review_enabled",
        "qa_host_parallel_fanout_enabled",
        "qa_host_duplicate_prep_guard_enabled",
        "qa_server_llm_enabled",
        "qa_host_llm_sampling_enabled",
        "qa_host_risk_review_enabled",
        "qa_host_test_plan_review_enabled",
        "qa_host_checklist_review_enabled",
        "qa_host_checklist_nli_suppress_enabled",
        "qa_host_comment_reconcile_suppress_enabled",
        "qa_prep_sliding_ttl_enabled",
        "qa_prep_disclose_unfinished",
        "qa_qualified_tc_ids_enabled",
        "qa_dup_shortlist_enabled",
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

    @field_validator("qa_generation_mode", mode="before")
    @classmethod
    def _coerce_generation_mode(cls, v: object) -> str:
        """QA_GENERATION_MODE is HARDCODED to "host" -- .env is not read.

        2026-08-01: test-case generation must never fall back to a
        server-side CLI/API/cursor backend, on this install or any other
        qa-agent-pro install built from this tree. Every input value
        (server/host/auto, valid or not, including unset) resolves to "host".
        This only affects resolve_generation_mode()'s three call sites in
        tools/mcp_handlers.py (test-case generation); it does not touch the
        other agents, which call llm.py directly and never read this field.
        """
        if v not in (None, "host"):
            logger.info(
                "QA_GENERATION_MODE=%r ignored -- test-case generation is "
                "chat-only; always resolving to 'host'",
                v,
            )
        return "host"

    @field_validator(
        "qa_host_ambiguity_review_enabled",
        "qa_host_ac_review_enabled",
        "qa_host_image_description_enabled",
        mode="before",
    )
    @classmethod
    def _force_host_boomerang_on(cls, v: object, info) -> bool:
        """These three flags are what makes host-mode generation ACTUALLY
        chat-only (see qa_generation_mode's WHY above): with any one of them
        OFF, qa_prepare_test_cases still makes a server-side llm.ask_json /
        ask_vision call for the ambiguity gate, AC synthesis, or image
        description respectively. 2026-08-01: hardcoded ON, ignoring .env
        entirely, for the same reason QA_GENERATION_MODE is hardcoded to
        "host" -- no combination of settings may reintroduce a server-side
        LLM call on the test-case generation path.
        """
        if v not in (None, True, "true", "True", "1", 1):
            logger.info(
                "%s=%r ignored -- host-mode generation is chat-only; "
                "always forcing this flag ON",
                info.field_name.upper(),
                v,
            )
        return True

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
        "qa_comment_reconcile_field_threshold",
        "qa_comment_reconcile_dedup_threshold",
        "qa_host_dedup_max_removal_ratio",
        "qa_host_dedup_low_text_ratio",
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

    @field_validator(
        "qa_token_price_generation_input_per_1m",
        "qa_token_price_generation_output_per_1m",
        "qa_token_price_classifier_input_per_1m",
        "qa_token_price_classifier_output_per_1m",
        "qa_token_price_cache_read_discount",
        "qa_token_price_cache_write_multiplier",
        mode="before",
    )
    @classmethod
    def _coerce_token_price(cls, v: object, info) -> float:
        """Lenient, never-raising float coercion for the token price table.

        Mirrors _coerce_reconcile_threshold's shape, plus a floor: a NEGATIVE
        rate would make the cost estimate SUBTRACT spend, which is never
        meaningful, so it is clamped to 0.0 with a WARNING instead of being
        honoured.
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
        if parsed < 0:
            logger.warning(
                "Invalid %s=%r — a negative price would subtract cost; using 0.0",
                info.field_name.upper(),
                v,
            )
            return 0.0
        return parsed

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
        "jira_max_sibling_chars",
        "jira_max_sibling_stories",
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
        "qa_prep_ttl_s",
        "qa_prep_max_bytes",
        "qa_prep_max_lifetime_s",
        "qa_host_dedup_max_groups",
        "qa_host_dedup_max_group_size",
        "qa_host_coverage_max_items",
        "qa_host_coverage_max_tc_per_item",
        "qa_category_stall_s",
        "qa_category_stall_strikes",
        "qa_ambiguity_cache_ttl_s",
        "qa_host_duplicate_prep_window_s",
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

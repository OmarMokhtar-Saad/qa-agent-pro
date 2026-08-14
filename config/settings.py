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
        # NB: qa_category_stall_s is intentionally ABSENT -- 0 is its documented
        # kill-switch, and membership here would rewrite it to the default.
        "qa_category_stall_strikes",
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

    # Cheaper/faster model for the intent router's classification pass (T-04 /
    # I-027). Empty string means "use qa_llm_model" (no override). Set to a haiku
    # model to cut routing cost — the classifier is a tiny, low-stakes call.
    qa_classifier_model: str = "claude-haiku-4-5"

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

    # ---- Anthropic prompt caching for the category fan-out (api backend) ----
    # REMOVED 2026-08-13 (flag-surface reduction, batch 8a):
    # QA_PROMPT_CACHE_ENABLED was DELETED and the shared cached prompt prefix
    # hardcoded OFF. It was an unvalidated experiment whose runbook rollout gate
    # was never run, and it is now doubly moot: generation is chat-only, so the
    # server-side 8-category fan-out it existed to make cheaper does not run on
    # the tester path at all (tools/mcp_handlers already passes warm_cache=False
    # on every host prepare). The assembled prompt is the pre-cache path on all
    # three backends, byte for byte. The surviving seam is
    # llm._prompt_cache_enabled(), which reads NO setting; the two numeric knobs
    # below are retained with the machinery they bound so a revival is one line.
    # See docs/FEATURE_FLAGS.md.
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

    # Still read: used ONLY to recognise a self-hosted Jira on a custom domain
    # (tickets.example.com) as a ticket URL rather than a generic web page.
    jira_base_url: str = ""
    # DEPRECATED 2026-08-01 for TICKET TEXT, and still inert for it. The
    # REST/Basic-Auth Jira path was removed in favour of the calling agent's own
    # Atlassian MCP connection (OAuth 2.1, Jira Cloud), so no text path reads
    # these. They are also kept so an existing .env carrying JIRA_EMAIL /
    # JIRA_API_TOKEN still loads cleanly instead of tripping validation on
    # upgrade.
    #
    # 2026-08-13 -- NO reader again, and the original sentence holds in full.
    # QA_JIRA_ATTACHMENT_FETCH_ENABLED was DELETED and the credentialed
    # attachment fetch hardcoded OFF (flag-surface reduction, batch 6), so
    # tools/jira_attachments.enabled() returns the False constant and nothing in
    # this tree sends this pair anywhere: a stale token cannot silently be used.
    # The fields are kept only so an existing .env carrying them still loads.
    # See docs/FEATURE_FLAGS.md.
    jira_api_token: str = ""
    jira_email: str = ""
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

    # --- Jira attachment BYTE retrieval -- REMOVED 2026-08-13. --------------
    # QA_JIRA_ATTACHMENT_FETCH_ENABLED was DELETED and the behaviour hardcoded
    # OFF (flag-surface reduction, batch 6): a kill-switch is no longer a
    # tester-editable .env value, because .env holds credentials and paths and
    # not behaviour. tools/jira_attachments.enabled() returns the False constant,
    # so nothing in this tree makes the credentialed
    # /rest/api/3/attachment/content/{id} request and a ticket's screenshots
    # reach a model only when a tester attaches them to the chat. See
    # docs/FEATURE_FLAGS.md.

    # Direct chat image uploads (screenshots/mockups attached to a message,
    # independent of Jira) — see tools/image_description.py -> llm.ask_vision()
    # (api backend only, same pipeline as Jira ticket images). These two caps
    # bound what the pipeline will actually read from an uploaded image.
    qa_max_chat_images: int = 3
    qa_max_chat_image_bytes: int = 5_000_000  # Anthropic's own per-image vision cap
    # Mobile device capture -> test cases -- UNCONDITIONAL since 2026-08-13
    # (flag-surface reduction, batch 7 (needs-config)): QA_MOBILE_CAPTURE was
    # DELETED and the behaviour hardcoded to the value the DISTRIBUTION ships
    # (`true`), NOT this field's code default (`False`) -- the same divergence,
    # and the same reasoning, batch 6 recorded for QA_SWAGGER_ENABLED. Testers
    # can always list attached Android/iOS devices, pick one, and capture
    # screens; tools/mcp_handlers._mobile_capture() returns the True constant.
    # qa_capture_screens makes NO server-side vision call (the screens ride to
    # the tester's own chat model as MCP image content), so that path needs no
    # credential; the Feature-Analysis mobile modes still call llm.ask_vision(),
    # which needs ANTHROPIC_API_KEY regardless of QA_LLM_BACKEND. Device
    # discovery/capture is bounded by the two timeouts below. See
    # docs/FEATURE_FLAGS.md.

    # Timeout (seconds) for device-discovery commands (adb devices / simctl list).
    qa_device_command_timeout: int = 20
    # Timeout (seconds) for a single screenshot capture (larger -- image transfer).
    qa_device_screenshot_timeout: int = 60

    # --- Mobile Device Testing (Maestro) — RETIRED as a setting 2026-08-13. ---
    # QA_MAESTRO_ENABLED was DELETED and the behaviour hardcoded OFF
    # (flag-surface reduction, batch 7 (needs-config)). Unlike the three flags
    # in that batch pinned ON, this one was never shipped in the public
    # distribution's .env template at all: it needs the Maestro CLI plus an
    # attached device or simulator on the operator's own machine, so it was a
    # private-checkout capability, and .env now holds credentials, paths and
    # per-install identifiers only. handle_run_mobile_suite and every mode it
    # drives are RETAINED and still registered in the full edition, but
    # tools/mcp_handlers._maestro_enabled() returns the False constant, so the
    # tool refuses. See docs/FEATURE_FLAGS.md.
    # Maestro runner dry-run -- UNCONDITIONAL since 2026-08-13 (flag-surface
    # reduction, batch 6): QA_MAESTRO_DRY_RUN was DELETED and the dry run
    # hardcoded ON, so maestro_runner._dry_run() returns the True constant and
    # the runner always PRINTS the command instead of executing on a device.
    # See docs/FEATURE_FLAGS.md.
    # Maestro CLI binary, flow output directory, and per-run timeout (seconds).
    qa_maestro_binary: str = "maestro"
    qa_maestro_flow_dir: str = "maestro_flows"
    qa_maestro_run_timeout: int = 600
    # AI-assisted fail→diagnose→patch→rerun heal loop (mode c) -- REMOVED as a
    # setting 2026-08-13 (flag-surface reduction, batch 7 (needs-config)):
    # QA_MAESTRO_HEAL_ENABLED was DELETED and hardcoded OFF, so
    # tools/maestro_healer.enabled() returns the False constant and the loop
    # never runs. The attempt bound below is retained with the loop it bounds.
    qa_maestro_heal_max_attempts: int = 2
    # AI exploratory run (Layer 3 -- observe->decide->act) -- REMOVED as a
    # setting 2026-08-13 (flag-surface reduction, batch 7 (needs-config)):
    # QA_MAESTRO_EXPLORE_ENABLED was DELETED and hardcoded OFF, so
    # tools/maestro_explorer.enabled() returns the False constant and the 4th
    # "🧭 AI exploratory run" mode is never offered. The step budget and
    # per-step timeout below are retained with the loop they bound; each
    # per-step device action was already unconditionally dry-run since
    # 2026-08-13, when QA_MAESTRO_DRY_RUN was deleted.
    qa_maestro_explore_max_steps: int = 15
    qa_maestro_explore_step_timeout: int = 60
    # LLM step translation (Layer 1 upgrade) -- REMOVED 2026-08-13 (flag-surface
    # reduction, batch 8a): QA_MAESTRO_TRANSLATE_ENABLED was DELETED and
    # hardcoded to its default, False. It was already INERT on the MCP surface --
    # its only caller was the retired Chainlit export path, so `qa-doctor` had to
    # report the flag itself as having no effect -- and Maestro was retired
    # wholesale in batch 7. tools/maestro_exporter.translate_suite_steps survives
    # behind the seam translate_enabled() for whoever re-wires the export path.
    # See docs/FEATURE_FLAGS.md.
    qa_maestro_translate_concurrency: int = 3
    # Test-account credentials for the Maestro login/recovery subflow. Injected as
    # Maestro env vars at RUN time (never written into YAML). .env only.
    qa_test_user: str = ""
    qa_test_password: str = ""

    # --- Web Suite Execution -- REMOVED 2026-08-13. ---
    # QA_WEB_RUN_ENABLED and QA_WEB_RUN_DRY_RUN were DELETED and hardcoded to
    # their defaults, OFF and ON (flag-surface reduction, batch 6), so
    # qa_run_web_suite / qa_submit_web_run always refuse and tools/web_runner.py
    # never launches a browser. The bounds below survive as tuning knobs for
    # whoever revives the runner in code; they gate nothing on their own.
    # See docs/FEATURE_FLAGS.md.
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

    # Web search grounding -- REMOVED 2026-08-13 (flag-surface reduction,
    # batch 6): QA_WEB_SEARCH_ENABLED was DELETED and hardcoded OFF, so
    # tools/web_search.search_web always returns "Web search disabled" and no
    # feature or ticket text leaves the org for a third-party search API.
    # See docs/FEATURE_FLAGS.md.

    # Structured coverage critic + remediation (T-08): after the initial
    # fan-out a structured critique runs and, if gaps are found, ONE
    # supplemental generation pass fills them (merged + re-deduped + re-scored).
    # UNCONDITIONAL since 2026-08-12 -- QA_COVERAGE_REGEN_ENABLED was DELETED
    # (flag-surface reduction, batch 3); see docs/FEATURE_FLAGS.md.
    # Bound on the critic->generate remediation loop (was a hardcoded
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

    # API test agent (chat-only): qa_prepare/submit/write_api_test. All OFF by
    # default; the write path is dry-run-first (qa_api_framework_write_dry_run).
    qa_api_test_enabled: bool = False
    qa_api_framework_write_enabled: bool = False
    qa_api_framework_path: str = ""
    qa_api_framework_write_dry_run: bool = True

    # Per-phase breakdown + $ cost estimate on the meter line -- UNCONDITIONAL
    # since 2026-08-13 (flag-surface reduction, batch 5): QA_TOKEN_METER_DETAIL_ENABLED
    # and QA_TOKEN_METER_COST_ENABLED were DELETED, both deletion candidates per
    # tools/flag_registry.py's own rationale rather than real experiments. Neither
    # gates a real behavioural risk -- no external call, no write -- so both are
    # now always shown alongside the (already unflagged) base meter line. See
    # docs/FEATURE_FLAGS.md.

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

    # Spec-document ingestion -- REMOVED 2026-08-13 (flag-surface reduction,
    # batch 8a): QA_SPEC_INGEST_ENABLED was DELETED and hardcoded to its default,
    # False, so tools/doc_ingest.ingest_document() always refuses and no attached
    # PDF/DOCX/TXT/MD is ever extracted into the generation prompt. The quality
    # gain was never measured. The module and its optional `spec` extra are
    # retained behind the seam tools.doc_ingest.enabled().
    #
    # QA_SPEC_RAG_PERSIST went with it, hardcoded to `True` on the maintainer's
    # instruction, and the honest note is that this is a DOCUMENTED NO-OP: the
    # field had NO reader anywhere in the tree even before this batch, and the
    # corpus write it once described can only have happened while ingestion was
    # on -- which it now never is. It is recorded here rather than silently
    # dropped so nobody re-derives the value from an empty grep.
    # See docs/FEATURE_FLAGS.md.
    # Raw upload byte cap and extracted-text char cap (mirror the image caps).
    # Raw upload byte cap and extracted-text char cap (mirror the image caps).
    qa_max_spec_bytes: int = 10_000_000
    qa_max_spec_chars: int = 20_000

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

    # Zephyr for Jira import export (Batch 4 -- tools/zephyr_exporter.py).
    # Zephyr for Jira import export -- REMOVED 2026-08-13 (flag-surface
    # reduction, batch 8a): QA_ZEPHYR_EXPORT_ENABLED was DELETED and hardcoded to
    # its default, False. `zephyr` never joins the qa_export_suite format list,
    # the elicitation picker and markdown menu are byte-identical to before the
    # feature existed, and the auto-export path writes no workbook pair. The
    # 15-column layout was never verified against a live Zephyr importer, which
    # is why its runbook pilot gate exists and why it was never promoted.
    # tools/zephyr_exporter.py is retained and still directly tested; the seam is
    # tools.mcp_handlers._zephyr_export_enabled(). See docs/FEATURE_FLAGS.md.

    # Dry run for that export -- UNCONDITIONAL since 2026-08-13 (flag-surface
    # reduction, batch 6): QA_ZEPHYR_DRY_RUN was DELETED and hardcoded ON. The
    # IMPORT into Jira is the external write, performed by the tester on our
    # artifact, and the column layout is still not vendor-verified
    # (operations/runbook.md -> "Zephyr export pilot gate"), so the artifact
    # stays bounded rather than suppressed: the workbook holds ONE case (the
    # first multi-step one, so the multi-row layout is actually exercised), is
    # named zephyr_import_PILOT.xlsx inside a zephyr_pilot_* folder, and the
    # reply tells the tester to import it into a SANDBOX project first. Lifting
    # that bound is now a code change, not an .env line. See
    # docs/FEATURE_FLAGS.md.

    # Distribution / test-cases-only mode. When ON, the UI exposes ONLY the
    # test-case generation flows (feature text / Jira / web URL / Swagger link
    # / mobile screens); bug-report, exploratory-coach, Maestro and fine-tune
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
    # --- Host-boomerang migration flags: DELETED 2026-08-12 ---------------
    # QA_HOST_RISK_REVIEW_ENABLED, QA_HOST_TEST_PLAN_REVIEW_ENABLED,
    # QA_HOST_CHECKLIST_REVIEW_ENABLED, QA_HOST_CHECKLIST_NLI_SUPPRESS_ENABLED
    # and QA_HOST_COMMENT_RECONCILE_SUPPRESS_ENABLED were MIGRATION flags, not
    # features, and every ledger row they governed is terminal. On 2026-08-12
    # they were deleted and their ON behaviour hardcoded: on the host path this
    # server makes no risk-scoring, test-plan, requirement-decomposition,
    # checklist NLI/adjudication or Jira comment-extraction call. Each stays
    # AND-ed with the pre-existing, default-OFF feature flag it rides on
    # (QA_LLM_RISK_SCORING / QA_TEST_PLAN_ARTIFACTS / QA_ATOMIC_CHECKLIST_ENABLED
    # / QA_CHECKLIST_NLI_ENABLED / QA_COMMENT_RECONCILE_ENABLED), so a default
    # install is unchanged, and every disclosure they drove is unchanged too.
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
        "qa_api_framework_write_enabled",
        "qa_api_framework_write_dry_run",
        "qa_ac_anchoring_enforce",
        "qa_feature_analysis_enabled",
        "qa_test_plan_artifacts",
        "qa_llm_risk_scoring",
        "jira_fetch_comments",
        "qa_comment_reconcile_enabled",
        "jira_fetch_images",
        "jira_fetch_parent",
        "jira_fetch_sibling_stories",
        "qa_dist_mode",
        "qa_mcp_enabled",
        "qa_update_require_signature",
        "qa_llm_strict_host",
        "qa_host_ambiguity_require_result",
        "qa_host_image_require_relevant",
        "qa_host_dedup_apply",
        "qa_server_llm_enabled",
        "qa_atomic_checklist_enabled",
        "qa_checklist_nli_enabled",
        "qa_checklist_adjudicate_enabled",
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
        "qa_prep_ttl_s",
        "qa_prep_max_bytes",
        "qa_prep_max_lifetime_s",
        "qa_host_dedup_max_groups",
        "qa_host_dedup_max_group_size",
        "qa_category_stall_s",
        "qa_category_stall_strikes",
        "qa_ambiguity_cache_ttl_s",
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

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
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("qa_agents.settings")

# Load .env into os.environ so libraries that read the environment directly
# (langsmith / langgraph) also see the values. BaseSettings additionally reads
# the same file for its own declared fields below.
load_dotenv()


def _lenient_bool(value: object) -> bool:
    """Parse a bool the same way the pre-pydantic settings did — never raises.

    Anything not in the truthy set is False, so a stray value can never crash
    import (mirrors the historical ``.lower() in ("1","true","yes")`` behaviour).
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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

    # Cheaper/faster model for the intent router's classification pass (T-04 /
    # I-027). Empty string means "use qa_llm_model" (no override). Set to a haiku
    # model to cut routing cost — the classifier is a tiny, low-stakes call.
    qa_classifier_model: str = "claude-haiku-4-5"

    jira_base_url: str = ""
    jira_api_token: str = ""
    jira_email: str = ""
    # Jira custom-field id that holds Acceptance Criteria. Defaults to the common
    # Jira Software default; different instances use different ids, so make it
    # configurable (QW-11 / I-023 / B-015). When empty on a ticket, jira_fetcher
    # falls back to scanning the description for an "Acceptance Criteria" heading.
    jira_ac_field: str = "customfield_10016"

    # Ticket comments are a second REST call (/issue/{key}/comment). Off by
    # default, same as this file's other opt-in fetches — flip on in .env once
    # validated, so the base issue fetch's existing behaviour (and every test
    # exercising it) is unaffected until an operator explicitly wants it.
    jira_fetch_comments: bool = False
    jira_max_comments: int = 5

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
    # (api backend only, same pipeline as Jira ticket images). The Chainlit
    # upload widget itself is always enabled (.chainlit/config.toml); these two
    # caps just bound what the app will actually read from an upload.
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
    # Chain-of-Thought reasoning stage per category (Feature 1 / CoT). When ON,
    # each category's single ask_json call is asked to FIRST enumerate what to test
    # (fields, limits, risks, attack vectors) into an internal ``analysis`` field,
    # THEN derive its test_cases from that reasoning -- one call, no extra
    # round-trip. ``analysis`` is discarded after generation (never shown to
    # testers). Off by default: when OFF the assembled prompt and the response model
    # are byte-identical to the pre-feature path. Adds only output tokens, so mind
    # the cli/cursor per-category timeout (_CATEGORY_TIMEOUT).
    qa_cot_reasoning_enabled: bool = False

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
    qa_ambiguity_gate_severity: str = "high"

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

    # Path to a bcrypt-hashed users file (JSON: {"username": "$2b$12$..."}) used
    # by app.py's @cl.password_auth_callback (tools/auth.py). A missing file means
    # auth is still enforced but no user can log in (fail-closed) rather than
    # crashing at import time — see tools/auth.py::verify_user. (QW-4)
    qa_auth_users_path: str = "operations/auth/users.json"
    # Password login toggle. Defaults ON (secure): unlike feature flags, turning
    # this OFF weakens security, so it must be an explicit local opt-out
    # (QA_AUTH_ENABLED=false) -- e.g. a single-user laptop setup.
    qa_auth_enabled: bool = True

    # Informational mirror of the Chainlit CORS allowlist configured in
    # .chainlit/config.toml's `allow_origins` (Chainlit reads that file directly
    # and does not expand env vars, so this cannot drive it automatically — it
    # exists so operators have one place to audit the intended origin list). (QW-4)
    qa_allowed_origins: str = "http://localhost:8000"

    # MCP server (mcp_server.py) exposing the QA agents/tools to Claude
    # Desktop / Claude Code / Cursor over stdio. Off by default like every
    # other feature gate: turning it on lets an MCP client drive generation,
    # exports, and (dry-run-defaulted) device runs, bypassing the Chainlit
    # auth AND rate-limit layer -- so each MCP tool call is separately audited.
    # Needs the optional extra:  pip install -e ".[mcp]".
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
    # before Chainlit starts. Any failure never blocks startup (see updater.py).
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

    @field_validator(
        "qa_web_search_enabled",
        "qa_rag_enabled",
        "qa_token_meter_enabled",
        "qa_coverage_regen_enabled",
        "qa_cot_reasoning_enabled",
        "qa_mutation_eval_enabled",
        "qa_feature_analysis_enabled",
        "testrail_dry_run",
        "jira_fetch_comments",
        "jira_fetch_images",
        "qa_mobile_capture",
        "qa_auth_enabled",
        "qa_maestro_enabled",
        "qa_maestro_dry_run",
        "qa_maestro_heal_enabled",
        "qa_maestro_explore_enabled",
        "qa_maestro_translate_enabled",
        "qa_spec_ingest_enabled",
        "qa_spec_rag_persist",
        "qa_finetune_export_enabled",
        "qa_swagger_enabled",
        "qa_dist_mode",
        "qa_mcp_enabled",
        "qa_mcp_elicit_enabled",
        "qa_auto_update_enabled",
        "qa_code_lock_enabled",
        "xray_dry_run",
        mode="before",
    )
    @classmethod
    def _coerce_bool(cls, v: object) -> bool:
        return _lenient_bool(v)

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
        "qa_max_chat_images",
        "qa_max_chat_image_bytes",
        "qa_device_command_timeout",
        "qa_device_screenshot_timeout",
        "qa_maestro_run_timeout",
        "qa_maestro_heal_max_attempts",
        "qa_maestro_explore_max_steps",
        "qa_maestro_explore_step_timeout",
        "qa_maestro_translate_concurrency",
        "qa_max_spec_bytes",
        "qa_max_spec_chars",
        "qa_llm_timeout_s",
        "qa_rag_recency_half_life_days",
        "qa_rag_max_entries",
        "qa_update_timeout",
        mode="before",
    )
    @classmethod
    def _coerce_jira_int(cls, v: object, info) -> int:
        default = cls.model_fields[info.field_name].default
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            logger.warning(
                "Invalid %s=%r — using default %d", info.field_name.upper(), v, default
            )
            return default


def _load_settings() -> Settings:
    """Build Settings, degrading to pure defaults if anything unexpectedly fails.

    The per-field coercers above already make individual bad values non-fatal;
    this is a final backstop so a truly broken environment can never turn a
    settings import into an application-wide crash (I-045 / B-017).
    """
    try:
        return Settings()
    except Exception as exc:  # pragma: no cover - defensive backstop
        logger.warning(
            "Settings failed to parse the environment (%s) — falling back to "
            "built-in defaults for all fields.",
            exc,
        )
        return Settings.model_construct()


settings = _load_settings()

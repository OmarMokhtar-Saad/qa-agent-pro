"""FastMCP server exposing the QA agents/tools to Claude Desktop, Claude Code,
and Cursor over stdio (gated by QA_MCP_ENABLED, default OFF).

Run:      python mcp_server.py        (or: python -m mcp_server)
Requires: pip install -e ".[mcp]"      (installs the optional ``fastmcp`` extra)

Each registered ``qa_*`` tool is a thin adapter that turns the FastMCP request
``Context`` into a plain async progress callback and delegates to
``tools/mcp_handlers.py`` — which holds all business logic, audit logging, and
concise-markdown shaping. No LLM call, prompt assembly, or secret handling lives
here; ``fastmcp`` is imported lazily inside ``build_server`` so this module (and
the mocked test suite) can load without the optional dependency installed.

NOTE: this module must NOT use ``from __future__ import annotations`` — string
annotations would make pydantic evaluate the ``ctx: Context`` hints against the
module globals, where the lazily-imported ``Context`` is not defined (NameError
at real FastMCP tool registration). Eager annotations resolve ``Context`` at
decoration time inside ``build_server``, where it is in scope.
"""

import logging
import os
import time
from pathlib import Path

# Anchor the working directory to the repo root BEFORE importing settings:
# config.settings loads the project .env from the cwd, and the data paths
# (suite store, corpus, audit log) are cwd-relative. MCP clients may spawn
# this server from an arbitrary directory, so re-anchor defensively.
os.chdir(Path(__file__).resolve().parent)

from config.settings import settings  # noqa: E402, I001
from tools import mcp_handlers  # noqa: E402
from tools import telemetry  # noqa: E402

logger = logging.getLogger("qa_agents.mcp")

SERVER_NAME = "qa-agent-pro"


# Latest MCP client identity from the initialize handshake (clientInfo),
# used to tag telemetry events with the host editor (cursor / claude-code).
_CLIENT = {"name": "", "version": ""}


def _note_client(ctx) -> None:
    """Forward the MCP client's name (initialize clientInfo) to the LLM layer
    so QA_LLM_BACKEND=auto can match the backend to the host editor, and
    record it (name + version) for telemetry tagging."""
    try:
        import llm

        info = ctx.session.client_params.clientInfo
        name = info.name
        llm.set_host_client(name)
        _CLIENT["name"] = (name or "").strip().lower()
        _CLIENT["version"] = str(getattr(info, "version", "") or "")
    except Exception:
        logger.debug("could not read clientInfo", exc_info=True)


async def _tracked(name, ctx, coro):
    """Await a tool handler while emitting a best-effort telemetry
    ``tool_called`` event (name, duration, ok/error_type, host client) and, on
    the dist path, a scrubbed ``capture_error_dist`` on failure. A per-tool
    ``$ai_trace_id`` is set so LLM ``$ai_generation`` events link to this call.
    Telemetry NEVER changes behaviour: the result or exception propagates
    unchanged and any metric failure is swallowed in the telemetry layer."""
    start = time.monotonic()
    ok = True
    error_type = None
    telemetry.start_tool_trace(name)
    try:
        return await coro
    except Exception as exc:
        ok = False
        error_type = type(exc).__name__
        telemetry.capture_error_dist(exc, tool=name, origin="mcp_tool")
        raise
    finally:
        telemetry.tool_called(
            name,
            duration_ms=int((time.monotonic() - start) * 1000),
            ok=ok,
            error_type=error_type,
            client_name=_CLIENT.get("name", ""),
            client_version=_CLIENT.get("version", ""),
            extra=telemetry.pop_tool_properties(),
        )


def _make_progress(ctx):
    """Adapt a FastMCP Context into the handlers' ``(message)->awaitable`` callback.

    Every call reports incremental progress so a long-running generation or
    device run resets the MCP client's tool-call timeout (progress notifications
    keep the stream alive). Best-effort — a transport hiccup never breaks the
    tool.
    """
    _note_client(ctx)  # every tool builds one of these — cheap host detection
    state = {"n": 0}

    async def progress(message: str) -> None:
        state["n"] += 1
        try:
            await ctx.report_progress(progress=state["n"], total=None, message=message)
        except Exception:
            logger.debug("mcp report_progress failed for %r", message, exc_info=True)

    return progress


def _make_chooser(ctx):
    """Adapt ``ctx.elicit`` into the handlers' ``choose(message, options)`` callback,
    mirroring ``_make_progress``. Returns ``None`` when QA_MCP_ELICIT_ENABLED is
    off so the retrofit tools keep today's non-interactive behaviour (the wizard
    treats a ``None``/unavailable chooser as 'render the markdown menu').

    Elicitation is supported only by some clients (Claude Code, Cursor — NOT
    Claude Desktop). A client without support makes ``ctx.elicit`` raise, which is
    caught here and reported as UNAVAILABLE so the caller falls back to markdown.
    """
    if not settings.qa_mcp_elicit_enabled:
        return None

    async def choose(message, options):
        try:
            result = await ctx.elicit(message, response_type=list(options))
        except Exception:
            logger.debug("mcp elicit unavailable for %r", message, exc_info=True)
            return mcp_handlers.ChoiceResult(mcp_handlers.UNAVAILABLE)
        action = getattr(result, "action", None)
        if action == "accept":
            return mcp_handlers.ChoiceResult(
                mcp_handlers.CHOSEN, value=getattr(result, "data", None)
            )
        return mcp_handlers.ChoiceResult(mcp_handlers.DECLINED)

    return choose


def _make_asker(ctx):
    """Adapt ``ctx.elicit`` into a free-text ``ask_text(message)`` callback —
    the text sibling of _make_chooser (same gating and degradation rules)."""
    if not settings.qa_mcp_elicit_enabled:
        return None

    async def ask_text(message):
        try:
            result = await ctx.elicit(message, response_type=str)
        except Exception:
            logger.debug("mcp elicit(text) unavailable for %r", message, exc_info=True)
            return mcp_handlers.ChoiceResult(mcp_handlers.UNAVAILABLE)
        action = getattr(result, "action", None)
        if action == "accept":
            return mcp_handlers.ChoiceResult(
                mcp_handlers.CHOSEN, value=getattr(result, "data", None)
            )
        return mcp_handlers.ChoiceResult(mcp_handlers.DECLINED)

    return ask_text


def _prepare_payload_to_content(result):
    """Convert a PreparePayloadResult into the content list qa_prepare_test_cases
    returns: one or more TEXT blocks (the grounded payload -- split across blocks
    only when it exceeds the per-block byte budget, NEVER truncated) plus one
    IMAGE block per forwarded ticket screenshot so the host's OWN multimodal model
    sees the real image (item 6). If the fastmcp Image API is unavailable at
    runtime the screenshot degrades to the text description already in the payload
    (image_context) plus a one-line note -- never a silent drop. mcp / fastmcp are
    imported lazily so importing this module never needs the optional extra."""
    from mcp.types import TextContent

    text_blocks, image_specs = mcp_handlers.assemble_prepare_payload(result)
    blocks: list = [TextContent(type="text", text=t) for t in text_blocks]
    for spec in image_specs:
        try:
            from fastmcp.utilities.types import Image

            mime = spec.get("mime") or "image/png"
            image = Image(data=spec["data"], format=(mime.split("/")[-1] or "png"))
            blocks.append(image.to_image_content(mime_type=mime))
        except Exception:
            logger.warning(
                "could not attach ticket screenshot %r as MCP image content -- "
                "falling back to its text description",
                spec.get("filename", "attachment"),
                exc_info=True,
            )
            blocks.append(
                TextContent(
                    type="text",
                    text=(
                        "> ℹ️  Screenshot "
                        f"'{spec.get('filename', 'attachment')}' could not be "
                        "attached as image content; its text description (if any) "
                        "is in the payload above."
                    ),
                )
            )
    return blocks


def build_server():
    """Construct and return the FastMCP server with every qa_* tool registered.

    ``fastmcp`` is imported here (not at module top level) so importing this
    module never requires the optional extra — only actually starting the server
    does.
    """
    from fastmcp import Context, FastMCP
    from mcp.types import ContentBlock

    mcp = FastMCP(SERVER_NAME)

    @mcp.tool()
    async def qa_generate_test_cases(
        ctx: Context, feature_or_url: str = "", proceed_anyway: bool = False
    ) -> str:
        """Generate a structured test suite. feature_or_url can be a feature
        description, a Jira/issue URL, a web page URL, or a Swagger/OpenAPI
        spec URL.

        When the user asks for test cases WITHOUT saying where the feature
        comes from, call this immediately with feature_or_url omitted — I will
        ask them myself (describe / Jira / web / Swagger / mobile screens /
        Jira + mobile) via a dialog or menu.

        Returns a concise markdown summary plus a persisted suite_id. Unless
        QA_AUTO_EXPORT_XLSX is turned off, the reply ALREADY contains the path
        to a finished .xlsx file: relay that path to the user as the
        deliverable and do NOT ask which export format they want or offer to
        push anywhere. Call qa_export_suite only when the user names a
        different format themselves.

        For an under-specified or no-UI ticket the reply may instead be a short
        list of clarifying questions (no suite is generated) — relay them to the
        user. Once they answer, call again with the fuller text, or set
        proceed_anyway=true to generate anyway with whatever is available.
        """
        return await _tracked(
            "qa_generate_test_cases",
            ctx,
            mcp_handlers.handle_generate_test_cases(
                feature_or_url,
                proceed_anyway=proceed_anyway,
                choose=_make_chooser(ctx),
                ask_text=_make_asker(ctx),
                progress=_make_progress(ctx),
            ),
        )

    @mcp.tool()
    async def qa_prepare_test_cases(
        ctx: Context, feature_or_url: str = "", proceed_anyway: bool = False
    ) -> list[ContentBlock]:
        """HOST-MODE generation. Instead of the server calling an LLM, THIS tool
        returns a grounded generation payload (a system prompt, the grounded
        feature/ticket context, a JSON schema, and 8 category instructions) for
        YOU, the host model, to run yourself.

        feature_or_url can be a feature description, a Jira/issue URL, a web page
        URL, or a Swagger/OpenAPI spec URL -- exactly like qa_generate_test_cases.

        WHAT TO DO WITH THE RESULT: generate the full test suite yourself from the
        returned payload (produce ONE JSON object matching the payload's
        response_schema, merging all categories into a single `test_cases`
        array), then call `qa_submit_suite` with the returned `prep_id` and your
        JSON. The server validates, de-duplicates, scores, exports and persists
        it, and replies with the finished suite + file path OR a short list of
        gaps to regenerate and resubmit under the SAME prep_id. A weaker model may
        submit one category at a time with `qa_submit_category` instead.

        If any ticket screenshots were available they are attached as image
        content -- inspect them directly. For an under-specified or no-UI ticket
        the reply may instead be clarifying questions (no payload); relay them, or
        pass proceed_anyway=true to prepare anyway.
        """
        result = await _tracked(
            "qa_prepare_test_cases",
            ctx,
            mcp_handlers.handle_prepare_test_cases(
                feature_or_url,
                proceed_anyway=proceed_anyway,
                choose=_make_chooser(ctx),
                ask_text=_make_asker(ctx),
                progress=_make_progress(ctx),
            ),
        )
        return _prepare_payload_to_content(result)

    @mcp.tool()
    async def qa_submit_suite(
        ctx: Context, prep_id: str = "", suite_json: str = ""
    ) -> str:
        """Submit a host-generated test suite back to the server to be validated,
        finalized, exported and persisted (the BACK half of host mode).

        Call this AFTER qa_prepare_test_cases: pass the `prep_id` it returned and
        `suite_json` -- the ONE JSON object you generated from the payload (a
        single merged `test_cases` array conforming to the payload's
        response_schema). The reply is EITHER the finished suite summary plus the
        exported file path, OR a short structured list of coverage gaps and vague
        cases to fix; if so, regenerate just those and call this again with the
        SAME prep_id. Relay the file path to the user as the deliverable; do not
        ask which export format they want.
        """
        return await _tracked(
            "qa_submit_suite",
            ctx,
            mcp_handlers.handle_submit_suite(
                prep_id,
                suite_json,
                ask_text=_make_asker(ctx),
                progress=_make_progress(ctx),
            ),
        )

    @mcp.tool()
    async def qa_submit_category(
        ctx: Context,
        prep_id: str = "",
        category_name: str = "",
        suite_json: str = "",
    ) -> str:
        """Submit ONE category's cases for a host that generates incrementally.

        Use this instead of qa_submit_suite when you produce the 8 categories in
        separate turns: pass the `prep_id` from qa_prepare_test_cases, the
        category name (e.g. \"Positive\", \"Negative\", \"Boundary\"), and
        `suite_json` for THAT category. Re-submitting a category REPLACES its
        earlier cases (newest wins). When every category is in, call
        qa_submit_suite with the same prep_id and an EMPTY suite_json to merge and
        finalize them.
        """
        return await _tracked(
            "qa_submit_category",
            ctx,
            mcp_handlers.handle_submit_category(
                prep_id,
                category_name,
                suite_json,
                progress=_make_progress(ctx),
            ),
        )

    @mcp.tool()
    async def qa_export_suite(
        ctx: Context, suite_id: str = "", format: str = ""
    ) -> str:
        """Export a previously generated suite (by suite_id) to one of:
        csv | xlsx | gherkin | playwright | testrail | zephyr.
        Returns the written file path. Reuses the stored suite; live-push dry-run
        defaults are preserved (this writes files, it never pushes to a TMS).

        `zephyr` is the Zephyr for Jira / Squad import pair: a 15-column workbook
        plus its zfj_import_config.json field map. It is accepted only when
        QA_ZEPHYR_EXPORT_ENABLED is on, and while QA_ZEPHYR_DRY_RUN is on (the
        default) it emits a single-case PILOT workbook meant for a sandbox
        project first, because the column layout is not vendor-verified yet.
        """
        return await _tracked(
            "qa_export_suite",
            ctx,
            mcp_handlers.handle_export_suite(
                suite_id,
                format,
                choose=_make_chooser(ctx),
                progress=_make_progress(ctx),
            ),
        )

    # Full edition only — the distribution build exposes test-case tools alone.
    if not mcp_handlers._test_cases_only():

        @mcp.tool()
        async def qa_bug_report(description: str, ctx: Context) -> str:
            """Turn a plain-language bug description into a structured bug report (markdown)."""
            return await _tracked(
                "qa_bug_report",
                ctx,
                mcp_handlers.handle_bug_report(
                    description, progress=_make_progress(ctx)
                ),
            )

        @mcp.tool()
        async def qa_explore_step(
            feature: str, session_id: str, ctx: Context, tester_response: str = ""
        ) -> str:
            """Advance an exploratory-testing coaching session one step at a time.

            Pass a stable session_id to keep coverage memory across calls; include
            tester_response with what you observed after the previous step.
            """
            return await _tracked(
                "qa_explore_step",
                ctx,
                mcp_handlers.handle_explore_step(
                    feature, session_id, tester_response, progress=_make_progress(ctx)
                ),
            )

    @mcp.tool()
    async def qa_search_corpus(
        query: str, ctx: Context, entry_type: str = "test_case", feature: str = ""
    ) -> str:
        """Search the RAG corpus (requires QA_RAG_ENABLED) for similar past test
        cases or bug reports. entry_type is 'test_case' or 'bug_report'; pass
        feature to narrow results to entries stored for that feature."""
        return await _tracked(
            "qa_search_corpus",
            ctx,
            mcp_handlers.handle_search_corpus(
                query, entry_type, feature, progress=_make_progress(ctx)
            ),
        )

    @mcp.tool()
    async def qa_configure_jira(
        ctx: Context, base_url: str = "", email: str = "", api_token: str = ""
    ) -> str:
        """Save Jira credentials into the agent's local .env so pasted ticket
        URLs work. Collect from the user: base_url (their Jira, e.g.
        https://company.atlassian.net), email (their Atlassian login), and
        api_token — the USER creates it at
        https://id.atlassian.com/manage-profile/security/api-tokens; never
        invent or reuse one. Values are stored locally only and never shown
        back. Afterwards run qa_setup_check to verify Jira shows configured."""
        return await _tracked(
            "qa_configure_jira",
            ctx,
            mcp_handlers.handle_configure_jira(
                base_url, email, api_token, progress=_make_progress(ctx)
            ),
        )

    @mcp.tool()
    async def qa_list_devices(ctx: Context) -> str:
        """List attached Android/iOS devices, emulators, and simulators."""
        return await _tracked(
            "qa_list_devices",
            ctx,
            mcp_handlers.handle_list_devices(progress=_make_progress(ctx)),
        )

    # Full edition only — Maestro runs + the multi-workflow wizard reference
    # modules the distribution build does not ship.
    if not mcp_handlers._test_cases_only():

        @mcp.tool()
        async def qa_run_mobile_suite(
            ctx: Context,
            mode: str = "",
            device_id: str = "",
            suite_id: str = "",
            app_id: str = "",
            goal: str = "",
        ) -> str:
            """Drive Maestro mobile testing (requires QA_MAESTRO_ENABLED). mode is one of:
            export (suite_id -> YAML flows in a per-suite dir),
            run (device_id + suite_id, reads that per-suite dir, dry-run default),
            heal (device_id + suite_id, requires QA_MAESTRO_HEAL_ENABLED), or
            explore (device_id + goal + app_id, requires QA_MAESTRO_EXPLORE_ENABLED).
            Per-device dry-run defaults are honoured."""
            return await _tracked(
                "qa_run_mobile_suite",
                ctx,
                mcp_handlers.handle_run_mobile_suite(
                    mode,
                    device_id=device_id,
                    suite_id=suite_id,
                    app_id=app_id,
                    goal=goal,
                    choose=_make_chooser(ctx),
                    ask_text=_make_asker(ctx),
                    progress=_make_progress(ctx),
                ),
            )

        @mcp.tool()
        async def qa_run_web_suite(
            ctx: Context,
            base_url: str = "",
            suite_id: str = "",
        ) -> str:
            """Run a generated suite step-by-step against a live web app in a
            real browser and report pass/fail per TC-ID (requires
            QA_WEB_RUN_ENABLED). Dry-run default previews the planned browser
            actions without launching a browser. Pass the base_url of the app
            under test and the suite_id from qa_generate_test_cases."""
            return await _tracked(
                "qa_run_web_suite",
                ctx,
                mcp_handlers.handle_run_web_suite(
                    base_url,
                    suite_id=suite_id,
                    choose=_make_chooser(ctx),
                    progress=_make_progress(ctx),
                ),
            )

        @mcp.tool()
        async def qa_wizard(ctx: Context) -> str:
            """Guided entry point for testers: pick a workflow (Test cases / Bug
            report / Exploratory / Mobile testing) and I walk you through it
            END-TO-END. Test cases asks where the feature comes from (describe it /
            Jira ticket / mobile screens / Jira + mobile), captures device screens
            when relevant, and returns the generated suite plus the Feature
            Analysis report. No tool names or parameters needed. On clients
            without MCP elicitation it returns a concise markdown menu instead."""
            return await _tracked(
                "qa_wizard",
                ctx,
                mcp_handlers.handle_wizard(
                    choose=_make_chooser(ctx),
                    ask_text=_make_asker(ctx),
                    progress=_make_progress(ctx),
                ),
            )

    @mcp.tool()
    async def qa_setup_check(ctx: Context) -> str:
        """Check whether THIS machine is ready: overall verdict, LLM backend
        auth, integrations, CLI tooling (adb/xcrun), enabled features and
        action items. Fast and read-only. Run this first on a new machine."""
        return await _tracked(
            "qa_setup_check",
            ctx,
            mcp_handlers.handle_setup_check(progress=_make_progress(ctx)),
        )

    # Optional tool — only registered when the Feature Analysis feature is on.
    if settings.qa_feature_analysis_enabled:

        @mcp.tool()
        async def qa_feature_analysis(
            ctx: Context,
            feature_or_url: str = "",
            mode: str = "",
            device_id: str = "",
        ) -> str:
            """Produce a compact enterprise Feature Analysis Report (requires
            QA_FEATURE_ANALYSIS_ENABLED). mode is one of: jira (analyse a feature
            description or Jira/issue URL), mobile (capture screens from a
            connected device, needs QA_MOBILE_CAPTURE), or jira_mobile (merge the
            ticket with captured screens). Omit mode and I'll ask; the mobile
            modes also ask for the device and offer a capture-another-screen
            loop."""
            return await _tracked(
                "qa_feature_analysis",
                ctx,
                mcp_handlers.handle_feature_analysis(
                    feature_or_url,
                    mode=mode,
                    device_id=device_id,
                    choose=_make_chooser(ctx),
                    progress=_make_progress(ctx),
                ),
            )

    return mcp


def main() -> None:
    """Entry point. Gated behind QA_MCP_ENABLED (default OFF) — with the flag off
    the server refuses to start rather than silently exposing the tools."""
    logging.basicConfig(level=logging.INFO)
    if not settings.qa_mcp_enabled:
        logger.warning(
            "QA_MCP_ENABLED is off — the MCP server will not start. "
            "Set QA_MCP_ENABLED=true in .env to enable it."
        )
        return
    telemetry.startup_notice()
    server = build_server()
    telemetry.server_start()

    def _prewarm_backend() -> None:
        # Warm the cursor-agent auth probe cache off the serving path: it can
        # take up to 20s and must never run while a request is in flight.
        try:
            import llm as _llm

            _llm._cursor_usable()
        except Exception:
            logger.debug("backend prewarm failed", exc_info=True)

    import threading

    threading.Thread(target=_prewarm_backend, daemon=True).start()
    logger.info("Starting the qa-agents MCP server over stdio…")
    server.run()


if __name__ == "__main__":
    main()

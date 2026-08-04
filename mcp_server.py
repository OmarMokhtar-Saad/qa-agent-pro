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

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

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


# The tester's OPEN workspace, per the MCP `roots` capability -- the only
# authoritative answer to "where is the project-scoped mcp.json?". Bounded on
# both axes: a client may legitimately report several folders, and the request is
# a round trip to a client that could accept it and never reply.
_MAX_WORKSPACE_ROOTS = 8
_ROOTS_TIMEOUT_S = 5.0


async def _workspace_roots(ctx) -> list[Path]:
    """The client's open workspace folder(s) as local filesystem paths.

    Needed because the project-scoped `.cursor/mcp.json` / `.mcp.json` a tester
    would actually edit lives in their editor workspace, which this stdio
    subprocess cannot otherwise locate: a dist install sits in a fixed
    directory, and `Path.cwd()` is useless here because this module chdir()s to
    its own install root at import time (see the top of the file). Two guesses
    shipped on that reasoning and BOTH were wrong in production (v1.31.0,
    v1.32.0); `roots` is the protocol's own answer.

    Best-effort and NEVER raises: `roots` is an OPTIONAL client capability, so an
    unsupported client can surface anything from a protocol error to an
    AttributeError to silence. Any failure returns [] and every caller degrades
    to its previous behaviour.
    """
    try:
        roots = await asyncio.wait_for(ctx.list_roots(), timeout=_ROOTS_TIMEOUT_S)
    except Exception:
        logger.debug("mcp list_roots unavailable", exc_info=True)
        return []
    try:
        reported = list(roots or [])[:_MAX_WORKSPACE_ROOTS]
    except Exception:
        logger.debug("mcp list_roots returned an unusable result", exc_info=True)
        return []

    out: list[Path] = []
    seen: set[str] = set()
    for root in reported:
        try:
            parsed = urlparse(str(getattr(root, "uri", "") or ""))
            # The MCP spec allows file:// only, and a host component other than
            # localhost is a remote/UNC location this process must not read.
            if parsed.scheme != "file":
                continue
            if (parsed.netloc or "").lower() not in ("", "localhost"):
                continue
            raw = unquote(parsed.path)  # %20 and friends
            if not raw:
                continue
            path = Path(raw)
        except Exception:
            logger.debug("skipping unusable MCP root %r", root, exc_info=True)
            continue
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


# ---- in-flight accounting for the drift restart (F1) -----------------------
# A hot restart must never land while a tool body is executing. _tracked is the
# single funnel every registered tool passes through, so the counter lives HERE
# rather than in a second wrapper.
#
# _DRAIN_IDLE_S additionally demands a quiet gap measured from the last tool to
# FINISH. 120s is chosen from the run this fix came from: the eight
# qa_submit_category calls arrived 37-41s apart, so a shorter gap would
# routinely exit in the middle of a tester's session. It still costs at most one
# deferral against the 15-minute tick.
_DRAIN_IDLE_S = 120.0
_DEFER_WARN_EVERY = 20
_INFLIGHT: dict = {"n": 0, "last_finish": 0.0}
_INFLIGHT_LOCK = threading.Lock()


def _drift_restart_enabled() -> bool:
    """Kill-switch for the drift restart. An ENV read, not a settings field, so
    an operator can disable it without the restart that settings would need."""
    raw = str(os.environ.get("QA_DRIFT_RESTART_ENABLED", "true")).strip().lower()
    return raw not in ("0", "false", "no", "off")


def _inflight_enter() -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT["n"] += 1


def _inflight_exit() -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT["n"] = max(0, _INFLIGHT["n"] - 1)
        _INFLIGHT["last_finish"] = time.monotonic()


def _drift_watch() -> None:
    """Dist only: replace this process when the install it runs from changes.

    WHY (F1): three MCP clients (Claude Desktop, Cursor, Claude Code) launch the
    SAME install dir. The launcher's watchdog restarts its child only when IT won
    the update race, so a peer client's update left this process serving stale
    code indefinitely -- observed 2026-07-29, a launcher still emitting the
    pre-7265baa "set read-only" wording while VERSION on disk read 1.10.7. The
    check lives in the CHILD, not the launcher, because only the child can see
    whether a tool is running.

    Scope of the guarantee: no TOOL BODY is executing. FastMCP's result
    serialization and non-tool traffic (tools/list, ping) are outside it, and the
    quiet gap is what covers them in practice.

    Never raises: every failure path just skips the tick, so this can only ever
    MISS a restart, never cause a spurious one.
    """
    try:
        from tools.mcp_handlers import (
            _DIST_UPDATE_REPO,
            _code_changed_since_start,
            _test_cases_only,
        )
        from tools.updater import _INSTALL_DIR, verify_integrity
    except Exception:
        logger.debug("drift watch unavailable", exc_info=True)
        return
    if not (_test_cases_only() and _DIST_UPDATE_REPO and _drift_restart_enabled()):
        return
    # 2026-08-04: the drift TICK no longer shares QA_UPDATE_INTERVAL_MINUTES'
    # 15-minute clock. This loop's steady-state cost is reading the local
    # VERSION/pyproject (no network; the manifest verify below runs only AFTER
    # a change is detected), so a fast tick is essentially free -- while it
    # rode the network cadence, a peer-applied release took up to 15 minutes
    # to reach the other clients' servers (v1.39.0 rollout: applied 09:04:30,
    # the two stale Cursor servers restarted only at their 09:07 marks). The
    # NETWORK check keeps its own 15-minute clock in the launcher's watchdog.
    try:
        interval = max(
            5.0, float(os.environ.get("QA_DRIFT_CHECK_SECONDS", "30"))
        )
    except (TypeError, ValueError):
        interval = 30.0
    deferrals = 0
    blocked = 0
    while True:
        time.sleep(interval)
        try:
            if not _code_changed_since_start():
                continue
            # apply_update overlays file-by-file in sorted order, so
            # pyproject.toml/VERSION can land BEFORE tools/. Exiting onto a
            # half-written tree would be worse than the staleness this fixes, so
            # wait until the tree verifies against its own manifest. This branch
            # escalates: a PERSISTENT mismatch (a locally edited file, a partial
            # update) would otherwise disable the restart for the life of the
            # process while logging a reassuring "waiting" line forever.
            mismatched = verify_integrity(Path(_INSTALL_DIR))
            if mismatched:
                blocked += 1
                if blocked % _DEFER_WARN_EVERY == 0:
                    logger.warning(
                        "drift: blocked for %d checks — %d file(s) still do not "
                        "match the manifest (%s). This is no longer a transient "
                        "update window; the new version will not be loaded.",
                        blocked,
                        len(mismatched),
                        ", ".join(sorted(mismatched)[:5]),
                    )
                else:
                    logger.info(
                        "drift: a new version is on disk but the tree does not "
                        "verify yet — waiting for the update to finish."
                    )
                continue
            blocked = 0
            # Logged BEFORE the lock: os._exit while holding it is safe (the
            # process dies), but a BLOCKING stderr write while holding it would
            # stall every _inflight_enter, i.e. every tool call.
            logger.info(
                "drift: installed version changed since this process loaded — "
                "restarting as soon as no tool is running."
            )
            # The exit happens UNDER the lock _inflight_enter also takes, so a
            # tool cannot slip in between the check and the exit.
            with _INFLIGHT_LOCK:
                busy = _INFLIGHT["n"]
                idle = time.monotonic() - _INFLIGHT["last_finish"]
                if not busy and idle >= _DRAIN_IDLE_S:
                    os._exit(86)
            deferrals += 1
            if deferrals % _DEFER_WARN_EVERY == 0:
                logger.warning(
                    "drift: restart deferred %d times — this install is not "
                    "picking up releases. Restart the editor to apply it.",
                    deferrals,
                )
            else:
                logger.info(
                    "drift: restart deferred (%d in flight, %.0fs since the last "
                    "tool finished, deferral #%d).",
                    busy,
                    idle,
                    deferrals,
                )
        except Exception:
            logger.debug("drift check failed", exc_info=True)


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
    _inflight_enter()
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
        # LAST in the finally: releasing the slot earlier would let a drift
        # restart fire while telemetry and FastMCP's result serialization still
        # had work to do, and os._exit does not flush buffered stdout.
        _inflight_exit()


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
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
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
                jira_content_json=jira_content_json,
            ),
        )

    @mcp.tool()
    async def qa_prepare_test_cases(
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
    ) -> list[ContentBlock]:
        """HOST-MODE generation. Instead of the server calling an LLM, THIS tool
        returns a grounded generation payload (a system prompt, the grounded
        feature/ticket context, a JSON schema, and 8 category instructions) for
        YOU, the host model, to run yourself.

        feature_or_url can be a feature description, a Jira/issue URL, a web page
        URL, or a Swagger/OpenAPI spec URL -- exactly like qa_generate_test_cases.

        JIRA URLS ARE A TWO-STEP BOOMERANG. This server holds no Jira
        credentials. For a Jira URL the FIRST reply is a DIRECTIVE telling you to
        call your OWN `mcp__atlassian__getJiraIssue` (and once more for the
        parent issue, when there is one), then call this tool AGAIN with the same
        feature_or_url plus `jira_content_json` set to the RAW JSON result. Do
        not summarise, translate or invent ticket content, and do not generate
        from the URL alone. If you have no `atlassian` MCP server connected, show
        the user the connection steps the directive includes.

        WHAT TO DO WITH THE RESULT: when the payload includes `orchestration`
        with mode `parallel_chat_workers`, fan out ONE same-session worker per
        category (Cursor Task / equivalent), then PREFER merging worker JSON and
        calling `qa_submit_suite` with the merged suite (Path B). Fallback Path A:
        `qa_submit_category` per category, `qa_prep_status` until ready, then
        `qa_submit_suite` with empty suite_json. Without orchestration, generate
        the full merged suite yourself and call `qa_submit_suite`. The server
        validates, de-duplicates, scores, exports and persists it, and replies
        with the finished suite + file path OR gaps to regenerate under the SAME
        prep_id. Use `qa_get_category_job` with category_name="all" for every
        worker packet in ONE call (or one name for a single packet).

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
                jira_content_json=jira_content_json,
            ),
        )
        return _prepare_payload_to_content(result)

    @mcp.tool()
    async def qa_submit_suite(
        ctx: Context, prep_id: str = "", suite_json: str | dict = ""
    ) -> str:
        """Submit a host-generated test suite back to the server to be validated,
        finalized, exported and persisted (the BACK half of host mode).

        Call this AFTER qa_prepare_test_cases: pass the `prep_id` it returned and
        `suite_json` -- the ONE JSON object you generated from the payload (a
        single merged `test_cases` array conforming to the payload's
        response_schema). Pass it as a JSON OBJECT when your client can send one
        -- there is no need to serialise it into a string first. A JSON string is
        still accepted unchanged, so either form works. The reply is EITHER the
        finished suite summary plus the
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
    async def qa_prep_status(ctx: Context, prep_id: str = "") -> str:
        """Show which categories are staged for a host-mode prep_id and whether
        Path A (empty suite_json) finalize is allowed yet.

        Use after parallel qa_submit_category calls. ready=yes means you may call
        qa_submit_suite with suite_json="". Path B (full merged suite_json) does
        not require ready=yes.
        """
        return await _tracked(
            "qa_prep_status",
            ctx,
            mcp_handlers.handle_prep_status(prep_id),
        )

    @mcp.tool()
    async def qa_get_category_job(
        ctx: Context, prep_id: str = "", category_name: str = ""
    ) -> str:
        """Return ONE self-contained category generation job for a prep_id
        (system_prompt + user_context + instruction + response_schema).

        Use when a same-session worker should not re-parse the full prepare
        payload. category_name should match orchestration.expected_categories.
        Pass category_name="all" (or "*") to get EVERY job in ONE call with
        the shared prompt blocks hoisted once -- preferred when dispatching
        parallel workers; never fetch packets one call per category.
        """
        return await _tracked(
            "qa_get_category_job",
            ctx,
            mcp_handlers.handle_get_category_job(prep_id, category_name),
        )

    @mcp.tool()
    async def qa_submit_category(
        ctx: Context,
        prep_id: str = "",
        category_name: str = "",
        suite_json: str | dict = "",
    ) -> str:
        """Submit ONE category's cases for a host that generates incrementally.

        Use this for Path A / incremental hosts (including parallel workers that
        stage one category each): pass the `prep_id` from qa_prepare_test_cases,
        the category name (canonical or known alias), and `suite_json` for THAT
        category -- as a JSON OBJECT when your client can send one, which avoids
        serialising a large payload into a string argument; a JSON string is still
        accepted unchanged. Names are normalized server-side. Re-submitting REPLACES that
        category (newest wins). When every expected category is staged, call
        qa_submit_suite with the same prep_id and an EMPTY suite_json. Prefer
        Path B (parent merge + full suite_json) when host dedup/coverage review
        matters. Check progress with qa_prep_status.
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
            """Start a structured bug report from a plain-language description.

            Chat-only: the server makes NO model call. It returns a task envelope
            (system_prompt + untrusted-wrapped context) that YOU answer, then you
            call qa_submit_bug_report with the task_id and your markdown report.
            """
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
            """Start the next step of an exploratory-testing coaching session.

            Chat-only: the server returns a task envelope that YOU answer, then you
            call qa_submit_explore_step with the task_id and your coaching step.

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
        async def qa_submit_bug_report(task_id: str, report: str, ctx: Context) -> str:
            """Submit the bug report YOU wrote for a task opened by qa_bug_report.

            qa_bug_report makes no model call: it hands you a task envelope. Write
            the report exactly as its system_prompt specifies, then call this with
            that task_id and the full markdown as `report`. The server validates
            the required sections, saves it to the corpus, and either returns the
            finished report or asks you to re-emit it against a NEW task id.
            """
            return await _tracked(
                "qa_submit_bug_report",
                ctx,
                mcp_handlers.handle_submit_bug_report(
                    task_id, report, progress=_make_progress(ctx)
                ),
            )

        @mcp.tool()
        async def qa_submit_explore_step(task_id: str, step: str, ctx: Context) -> str:
            """Submit the coaching step YOU wrote for a task opened by qa_explore_step.

            Include the trailing <meta>area: …; phase: …</meta> line the task's
            system_prompt asks for: the server parses it to track coverage, then
            strips it before the tester sees the step.
            """
            return await _tracked(
                "qa_submit_explore_step",
                ctx,
                mcp_handlers.handle_submit_explore_step(
                    task_id, step, progress=_make_progress(ctx)
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
        ctx: Context,
        base_url: str = "",
        email: str = "",
        api_token: str = "",
        atlassian_verify_json: str = "",
    ) -> str:
        """DEPRECATED as of 2026-08-01 — Jira needs no credentials here any more.

        Jira tickets are read through YOUR OWN Atlassian MCP connection
        (mcp.atlassian.com, OAuth, Jira Cloud). This tool now returns the
        per-client connection steps and stores NOTHING. Do not ask the user for
        an API token, and never invent one. If a ticket URL fails, call
        qa_prepare_test_cases and follow the directive it returns.

        It IS still useful for one thing: VERIFYING that connection. Called with
        no arguments it returns a directive telling you to call
        mcp__atlassian__atlassianUserInfo (read-only, no parameters). Call it
        again with atlassian_verify_json set to that call's RAW JSON result — or
        to {"error": "<what happened>"} when the call fails or the tool does not
        exist — and the server reports a real verified / not-connected verdict
        plus the exact connection steps for this editor. The result is read once
        and discarded; nothing is stored."""
        return await _tracked(
            "qa_configure_jira",
            ctx,
            mcp_handlers.handle_configure_jira(
                base_url,
                email,
                api_token,
                atlassian_verify_json=atlassian_verify_json,
                progress=_make_progress(ctx),
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
            """Start a run of a generated suite against a live web app in a
            real browser (requires QA_WEB_RUN_ENABLED).

            Chat-only: the server makes NO model call. It returns a task
            envelope carrying the suite's steps and a response_schema; YOU
            translate every case into whitelisted browser actions and call
            qa_submit_web_run with the task_id and that JSON — the browser run,
            the SSRF checks and the pass/fail report all happen there. Dry-run
            default previews the planned actions without launching a browser.
            Pass the base_url of the app under test and the suite_id from
            qa_generate_test_cases."""
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
        async def qa_submit_web_run(
            task_id: str, translations_json: str, ctx: Context
        ) -> str:
            """Submit the browser actions YOU translated for a task opened by
            qa_run_web_suite, and run the suite.

            qa_run_web_suite makes no model call: it hands you a task envelope
            with a system_prompt, the untrusted-wrapped test cases and a
            response_schema. Produce a SINGLE JSON object matching that schema —
            one entry per case in `translations`, echoing that case's tc_id
            EXACTLY, whose `actions` list is FLAT: every action carries the
            positive `step_number` of the step it belongs to, and there is NO
            per-step `steps` object (an entry with one is rejected and that case
            is not run) — then call this with the task_id and that JSON as
            `translations_json`. The server validates every action against its
            8-verb whitelist, re-validates the target URL, launches the browser
            (or previews the plan when dry-run is on) and reports pass/fail per
            TC-ID."""
            return await _tracked(
                "qa_submit_web_run",
                ctx,
                mcp_handlers.handle_submit_web_run(
                    task_id, translations_json, progress=_make_progress(ctx)
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
        progress = _make_progress(ctx)
        # Resolved BEFORE entering _tracked: this is a round trip back to the
        # client, not part of the report's own work, and _tracked owns the
        # in-flight counter that gates the drift restart.
        roots = await _workspace_roots(ctx)
        return await _tracked(
            "qa_setup_check",
            ctx,
            mcp_handlers.handle_setup_check(progress=progress, workspace_roots=roots),
        )

    # Optional tool — only in the FULL edition, and only when the Feature
    # Analysis feature is on. 2026-08-03: the public qa-agent-pro build is
    # deliberately test-cases-only AND credential-free, and this PAIR was the
    # last tester-facing path there that could reach a server-side LLM backend
    # (its `mobile` / `jira_mobile` modes describe captured screens through
    # this server's own ask_vision, tools/image_description.py). The edition
    # gate outranks the flag on purpose: install.sh seeds .env only when the
    # file is absent and updates never rewrite it, so every ALREADY-installed
    # dist still carries QA_FEATURE_ANALYSIS_ENABLED=true from an older
    # .env.example. Dropping the key from the template alone would leave those
    # installs exposing both tools.
    if settings.qa_feature_analysis_enabled and not mcp_handlers._test_cases_only():

        @mcp.tool()
        async def qa_feature_analysis(
            ctx: Context,
            feature_or_url: str = "",
            mode: str = "",
            device_id: str = "",
            jira_content_json: str = "",
        ) -> str:
            """Start a compact enterprise Feature Analysis Report (requires
            QA_FEATURE_ANALYSIS_ENABLED).

            Chat-only: the server makes NO model call. It returns a task envelope
            (system_prompt + untrusted-wrapped context + a response_schema) that
            YOU answer, then you call qa_submit_feature_analysis with the task_id
            and your JSON report.

            mode is one of: jira (analyse a feature description or Jira/issue
            URL), mobile (capture screens from a connected device, needs
            QA_MOBILE_CAPTURE), or jira_mobile (merge the ticket with captured
            screens). Omit mode and I'll ask; the mobile modes also ask for the
            device and offer a capture-another-screen loop, and their screenshot
            descriptions are still produced by this server's own vision call.

            For a Jira URL the reply may be a DIRECTIVE asking you to fetch the
            issue with your own mcp__atlassian__getJiraIssue tool and call again
            with jira_content_json set to its raw JSON result."""
            return await _tracked(
                "qa_feature_analysis",
                ctx,
                mcp_handlers.handle_feature_analysis(
                    feature_or_url,
                    mode=mode,
                    device_id=device_id,
                    choose=_make_chooser(ctx),
                    progress=_make_progress(ctx),
                    jira_content_json=jira_content_json,
                ),
            )

        @mcp.tool()
        async def qa_submit_feature_analysis(
            task_id: str, report_json: str, ctx: Context
        ) -> str:
            """Submit the Feature Analysis JSON YOU wrote for a task opened by
            qa_feature_analysis.

            qa_feature_analysis makes no model call: it hands you a task envelope
            carrying a system_prompt, an untrusted-wrapped user_context and a
            response_schema. Produce a SINGLE JSON object matching that schema,
            then call this with the task_id and the JSON as `report_json`. The
            server validates it, renders the report, and — if the submission
            carried no usable object — hands you ONE resubmit round against a new
            task_id."""
            return await _tracked(
                "qa_submit_feature_analysis",
                ctx,
                mcp_handlers.handle_submit_feature_analysis(
                    task_id, report_json, progress=_make_progress(ctx)
                ),
            )

    return mcp


def _configure_logging() -> None:
    """INFO+ to a rotating file under data/logs/; WARNING+ to stderr.

    Over stdio, MCP clients render EVERY stderr line as an error (Cursor logs
    "[error] INFO ..." for each httpx/telemetry line), which buries real
    failures in noise. Errors stay on stderr; the full INFO trail moves to a
    file an operator can tail. Never raises -- if the file handler cannot be
    created, stderr keeps INFO so nothing is lost."""
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(
        logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    )
    root.addHandler(stderr_handler)
    try:
        log_dir = Path(__file__).resolve().parent / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "qa-agents.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(file_handler)
    except Exception:
        stderr_handler.setLevel(logging.INFO)
        logger.warning(
            "Could not open data/logs/qa-agents.log -- keeping INFO on stderr."
        )
    # Third-party request logging is diagnostic noise at INFO (one line per
    # telemetry POST); real problems still surface at WARNING+. FastMCP is in
    # the list because it attaches its OWN rich handler (bypassing the root
    # config above), so its INFO transport banner still reached stderr on the
    # v1.38.0 validation run.
    for noisy in ("httpx", "httpcore", "FastMCP"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # The setLevel above is NOT enough for FastMCP: it (re)configures its own
    # handler and level during server.run(), which overrode this on the
    # v1.39.0 validation (the INFO transport banner still hit stderr at
    # 09:09:36). Its level is read from the environment, so pin it there --
    # setdefault keeps an operator's explicit choice.
    os.environ.setdefault("FASTMCP_LOG_LEVEL", "WARNING")


def main() -> None:
    """Entry point. Gated behind QA_MCP_ENABLED (default OFF) — with the flag off
    the server refuses to start rather than silently exposing the tools."""
    _configure_logging()
    if not settings.qa_mcp_enabled:
        logger.warning(
            "QA_MCP_ENABLED is off — the MCP server will not start. "
            "Set QA_MCP_ENABLED=true in .env to enable it."
        )
        return
    # Host-boomerang migration: if an operator flipped QA_SERVER_LLM_ENABLED off
    # while ledger rows are still unmigrated, those features are OFF rather than
    # boomeranged. Say so once, at startup, instead of letting it surface as the
    # ambiguity gate quietly stopping. No-op (and silent) with the flag ON.
    try:
        from tools.host_llm import warn_once_if_degraded

        warn_once_if_degraded()
    except Exception:  # pragma: no cover - a disclosure must never block boot
        logger.debug("server-LLM disclosure failed", exc_info=True)
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

    threading.Thread(target=_prewarm_backend, daemon=True).start()
    threading.Thread(target=_drift_watch, daemon=True).start()
    logger.info("Starting the qa-agents MCP server over stdio…")
    server.run(show_banner=False)


if __name__ == "__main__":
    main()

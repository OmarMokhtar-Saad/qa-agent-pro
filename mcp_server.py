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
import random
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
from tools import guidance  # noqa: E402
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
        # ...and to the per-client elicitation record, which keys its strikes by
        # this name so one editor's verdict can never gate another's on a shared
        # install. Never raises (see tools.mcp_handlers.note_elicit_client).
        mcp_handlers.note_elicit_client(name)
        # Tag this process's log lines with the editor that owns it: on an
        # install shared by three clients, the pid alone does not say WHICH.
        from tools.log_setup import set_client

        set_client(name, Path(__file__).resolve().parent / "data" / "logs")
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

# Exit code for a DELIBERATE drift restart. The dist launcher's supervisor
# matches this exact number in _pump_child_out to tell a version reload apart
# from a crash (LAUNCHER_TEMPLATE.DRIFT_EXIT_CODE in scripts/build_dist.py --
# the two literals MUST stay equal; tests/test_launcher_drift_exit.py asserts
# it, because the launcher is generated source and cannot import from here).
# 2026-08-09: without that match every release logged one
# "MCP server exited unexpectedly" per connected client, which MCP clients
# render as an error.
#
# 0 is deliberately NOT used: under the dist the editor never sees THIS
# process's exit code -- it sees the launcher's. This code is a private
# protocol with the supervising launcher, which must respawn and replay the
# handshake here, and 0 is indistinguishable from a normal shutdown.
DRIFT_RESTART_EXIT_CODE = 86
# Every client sharing one install detects the same peer update on the same
# tick, so they would otherwise all exit and respawn in the same second.
_DRIFT_EXIT_JITTER_S = 3.0
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
        interval = max(5.0, float(os.environ.get("QA_DRIFT_CHECK_SECONDS", "30")))
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
            # Jitter ONCE -- on the first tick that sees this drift (deferrals
            # is still 0) -- and OUTSIDE the lock. Under the lock a blocking
            # sleep would stall every _inflight_enter, i.e. every tool call; and
            # re-sleeping on each later deferral tick would only add latency to
            # a restart that is already waiting. One sleep is enough: its whole
            # job is to de-synchronise the peer clients that share this install
            # and detect the same update on the same tick.
            if deferrals == 0:
                try:
                    _jitter = random.uniform(0.0, _DRIFT_EXIT_JITTER_S)
                    # Guarded so a zero jitter never calls sleep: the drift-watch
                    # tests pin random.uniform to 0.0 and count sleep ticks.
                    if _jitter > 0:
                        time.sleep(_jitter)
                except Exception:  # a jitter failure must never skip the restart
                    logger.debug("drift exit jitter failed", exc_info=True)
            # The exit happens UNDER the lock _inflight_enter also takes, so a
            # tool cannot slip in between the check and the exit.
            with _INFLIGHT_LOCK:
                busy = _INFLIGHT["n"]
                idle = time.monotonic() - _INFLIGHT["last_finish"]
                if not busy and idle >= _DRAIN_IDLE_S:
                    os._exit(DRIFT_RESTART_EXIT_CODE)
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
    unchanged and any metric failure is swallowed in the telemetry layer.

    It also writes the ONE log line that names the tool (2026-08-19, F07).
    Before it, the only per-call trace in ``data/logs/qa-agents-<pid>.log`` was
    the MCP library's nameless ``Processing request of type CallToolRequest``:
    a 12-call live run left 11 calls unattributable, and a forensics script that
    looked for tool names in the log found none and picked no log at all. The
    line belongs HERE, not in each handler, because this is the one seam every
    tool already passes through and the name is a literal at each call site.

    WHY IT CANNOT LEAK A PAYLOAD: this function is handed a NAME and an already
    built coroutine. Ticket text, generated cases and test data are bound inside
    that coroutine and are not values this frame can see, so the containment is
    structural rather than a rule about what to format. Keep it that way -- do
    not add an ``args`` parameter to satisfy a future 'just log the prep_id'.

    One line, INFO, per call: the file already reaches ~220 KB on a busy install,
    and ``mcp.server.lowlevel.server`` is silenced in ``_configure_logging`` so
    the per-call volume is unchanged rather than doubled."""
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
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "tool %s: %s in %d ms",
            name,
            "ok" if ok else "error %s" % (error_type or "Exception"),
            duration_ms,
        )
        telemetry.tool_called(
            name,
            duration_ms=duration_ms,
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


def _feature_analysis_enabled() -> bool:
    """Always True -- Feature Analysis is ON, unconditionally.

    QA_FEATURE_ANALYSIS_ENABLED was DELETED on 2026-08-14 (flag-surface
    reduction, batch 8c) and hardcoded ON: the flag policy's "promote to
    default ON (flag deleted)" outcome for an experiment, taken by the
    maintainer ahead of the 2026-11-12 review date. A named seam, mirroring
    ``tools.mcp_handlers._feature_analysis_enabled``, so a revival is one
    line in each of two documented places. NOT settings-derived.

    This is the ONE batch in the programme that changes behaviour in the
    EXPANDING direction, so read the gate below carefully: the
    test-cases-only EDITION check still outranks this seam and is still
    evaluated, which is what keeps the credential-free public distribution
    from registering a pair whose mobile modes reach ``ask_vision``.
    """
    return True


def _elicit_enabled() -> bool:
    """Always True -- MCP elicitation dialogs are ON, unconditionally.

    QA_MCP_ELICIT_ENABLED was DELETED on 2026-08-13 (flag-surface reduction,
    batch 7 (needs-config)) and hardcoded to the value the DISTRIBUTION ships
    (`true`), not this field's code default. A named seam, mirroring
    ``tools.mcp_handlers._elicit_enabled``, so the non-interactive fallback
    below stays executable by its tests and a revival is one line in each of
    two documented places. NOT settings-derived.

    The per-CLIENT limitation this used to be confused with is unaffected and
    still handled below: a client that cannot show dialogs makes ``ctx.elicit``
    raise, which is caught and reported as UNAVAILABLE so the caller falls back
    to the markdown menu.
    """
    return True


def _make_chooser(ctx):
    """Adapt ``ctx.elicit`` into the handlers' ``choose(message, options)`` callback,
    mirroring ``_make_progress``. Returns ``None`` when ``_elicit_enabled()``
    is False so the retrofit tools keep the non-interactive behaviour (the wizard
    treats a ``None``/unavailable chooser as 'render the markdown menu').

    Elicitation is supported only by some clients (Claude Code, Cursor — NOT
    Claude Desktop). A client without support makes ``ctx.elicit`` raise, which is
    caught here and reported as UNAVAILABLE so the caller falls back to markdown.
    """
    if not _elicit_enabled():
        return None
    # 2026-08-21: and no dialog at all for a client whose elicitation transport
    # has proven unanswerable in this process (two consecutive timeouts, no
    # answer between them). None is the existing, tested "render the markdown
    # menu" path, so the caller degrades exactly as it does for a client with no
    # elicitation support -- but at 0s instead of 55s. Gated HERE rather than in
    # _make_elicitors so the single-sided call sites are covered too.
    if mcp_handlers.elicit_client_gated():
        return None

    _budget = {"deadline": time.monotonic() + mcp_handlers._ELICIT_CALL_BUDGET_S}

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

    choose._elicit_budget = _budget
    return choose


def _make_asker(ctx):
    """Adapt ``ctx.elicit`` into a free-text ``ask_text(message)`` callback —
    the text sibling of _make_chooser (same gating and degradation rules)."""
    if not _elicit_enabled():
        return None
    if mcp_handlers.elicit_client_gated():  # see _make_chooser
        return None

    _budget = {"deadline": time.monotonic() + mcp_handlers._ELICIT_CALL_BUDGET_S}

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

    ask_text._elicit_budget = _budget
    return ask_text


def _make_elicitors(ctx):
    """Build BOTH elicitation callbacks for one tool call, sharing ONE budget.

    K1b (2026-08-10). MCP dialogs chain sequentially inside a single tool call --
    the image gate asks twice, the wizard up to three times -- and most chains mix
    an enum dialog with a free-text one. Bounding each dialog separately still let
    one call run 110-220s and die at the client's ~120s idle timeout, so the budget
    has to be shared BETWEEN the two callbacks, which is only possible in a scope
    where both exist. That scope is this function.

    The holder is stamped EAGERLY here rather than at the first dialog: these
    callbacks are built as arguments to the handler coroutine, i.e. at the top of
    the tool body before ``_tracked`` awaits anything, so pre-dialog work inside the
    handler (device scans, the Jira fetch, generation) burns the same budget the
    dialogs do. That is the call-entry anchor, without threading a parameter through
    ~15 handler signatures.

    Read back by ``mcp_handlers._elicit_wait_s`` via ``cb._elicit_budget``.
    Single-sided call sites keep ``_make_chooser`` / ``_make_asker``: with only one
    callback in play, that factory's own private holder is already per-call correct.

    Returns a KWARGS DICT so a call site stays a single expression
    (``**_make_elicitors(ctx)``) -- a tuple would need a preceding statement and a
    restructure of every ``return await _tracked(...)`` it appears in.
    """
    choose = _make_chooser(ctx)
    ask_text = _make_asker(ctx)
    if choose is None or ask_text is None:
        return {"choose": choose, "ask_text": ask_text}
    budget = {"deadline": time.monotonic() + mcp_handlers._ELICIT_CALL_BUDGET_S}
    choose._elicit_budget = budget
    ask_text._elicit_budget = budget
    return {"choose": choose, "ask_text": ask_text}


def _image_content_blocks(image_specs):
    """Convert {filename, mime, data} specs into MCP image content blocks.

    Same lazy fastmcp import and the same NEVER-silent text fallback as
    _prepare_payload_to_content, which is deliberately left untouched so the
    prepare path stays byte-identical. Used by qa_capture_screens."""
    from mcp.types import TextContent

    blocks: list = []
    for spec in image_specs or []:
        try:
            from fastmcp.utilities.types import Image

            mime = spec.get("mime") or "image/png"
            image = Image(data=spec["data"], format=(mime.split("/")[-1] or "png"))
            blocks.append(image.to_image_content(mime_type=mime))
        except Exception:
            logger.warning(
                "could not attach captured screen %r as MCP image content",
                spec.get("filename", "screen"),
                exc_info=True,
            )
            blocks.append(
                TextContent(
                    type="text",
                    text=(
                        "> ℹ️  Captured screen "
                        f"'{spec.get('filename', 'screen')}' could not be "
                        "attached as image content."
                    ),
                )
            )
    return blocks


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


def _register_prompts(mcp, *, test_cases_only: bool, api_tests: bool) -> None:
    """Register one MCP Prompt per guidance workflow this edition can support.

    A loop rather than N decorated functions on purpose: the gate deciding
    WHICH prompts exist already lives in ``tools/guidance.prompt_texts``, and
    re-expressing it here as a second ladder of ``if`` statements is exactly how
    two copies of one rule drift apart. Each body is closed over through a
    factory -- closing over the loop variable directly would hand every prompt
    the LAST body, silently.
    """
    for name, body in guidance.prompt_texts(
        test_cases_only=test_cases_only, api_tests=api_tests
    ).items():

        def _make(text: str):
            def _prompt() -> str:
                return text

            return _prompt

        fn = _make(body)
        fn.__name__ = name
        description = guidance.prompt_description(name)
        fn.__doc__ = description
        mcp.prompt(name=name, description=description)(fn)


def build_server():
    """Construct and return the FastMCP server with every qa_* tool registered.

    ``fastmcp`` is imported here (not at module top level) so importing this
    module never requires the optional extra — only actually starting the server
    does.
    """
    from fastmcp import Context, FastMCP
    from mcp.types import ContentBlock

    # The two edition gates, read here so the GUIDANCE text is built from the
    # same expressions the registration gates below use. (Those gates still
    # call `_test_cases_only()` inline at their own sites; this is a second
    # read of one pure function, not a second source of truth.) The guidance
    # names tools, and a client that calls a tool this edition never registered
    # fails mid-workflow in front of a tester -- so the text and the
    # registration must not be able to disagree.
    edition_test_cases_only = mcp_handlers._test_cases_only()
    edition_api_tests = bool(settings.qa_api_test_enabled)

    mcp = FastMCP(
        SERVER_NAME,
        instructions=guidance.server_instructions(
            test_cases_only=edition_test_cases_only,
            api_tests=edition_api_tests,
        ),
    )

    @mcp.tool()
    async def qa_generate_test_cases(
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
        source_plan: str = "",
        attached_image_count: int = 0,
        capture_ids: list[str] | None = None,
        image_gate_ack: bool = False,
        image_carry_ack: bool = False,
    ) -> str:
        """Generate a structured test suite. feature_or_url can be a feature
        description, a Jira/issue URL, a web page URL, or a Swagger/OpenAPI
        spec URL.

        When the user asks for test cases WITHOUT saying where the feature
        comes from, call this immediately with feature_or_url omitted — I will
        ask them myself (describe / Jira / web / Swagger / mobile screens /
        Jira + mobile) via a dialog or menu.

        Returns a concise markdown summary plus a persisted suite_id. The reply
        ALREADY contains the path
        to a finished .xlsx file: relay that path to the user as the
        deliverable and do NOT ask which export format they want or offer to
        push anywhere. Call qa_export_suite only when the user names a
        different format themselves.

        For an under-specified or no-UI ticket the reply may instead be a short
        list of clarifying questions (no suite is generated) — relay them to the
        user. Once they answer, call again with the fuller text, or set
        proceed_anyway=true to generate anyway with whatever is available.

        IMAGE GATE (always on). This tool runs the
        generation in YOUR chat model. ASK FIRST: for a Jira URL, ask the USER
        where the ticket's SCREENS come from BEFORE your first call and pass
        `source_plan` on it -- this server cannot read images out of Jira, only
        text, and asking up front costs ZERO extra tool calls. Only pass
        `source_plan` if the user ANSWERED -- never guess it, and never send
        `image_gate_ack=true` unless the user explicitly said the screens do not
        matter: that pair skips BOTH asks, including the informed one that names
        the screens the fetched ticket really has. Without a plan the FIRST reply
        is ONLY that question (nothing fetched, nothing prepared) and you must
        call again with
        the SAME feature_or_url plus `source_plan` (`jira` = ticket text only,
        `jira_attach`, `jira_device`, `jira_both`, `device`); with `jira_attach`
        also pass `attached_image_count`, and with `jira_device` call
        `qa_capture_screens` first and pass its `capture_ids`. A second, informed
        reply may NAME the screens the fetched ticket has; supply them or pass
        `image_gate_ack=true` (send it together with `source_plan='jira'` up
        front when the user has already said the screens do not matter).

        RE-RUNNING THE SAME SOURCE: if a recent preparation for this source was
        grounded on screens and your new call carries none, this server either
        carries those screens forward itself or REFUSES and names them.
        `proceed_anyway=true` does NOT dismiss that -- re-send the screens, or
        pass `image_carry_ack=true` if the user really wants the cases written
        without them.
        """
        return await _tracked(
            "qa_generate_test_cases",
            ctx,
            mcp_handlers.handle_generate_test_cases(
                feature_or_url,
                proceed_anyway=proceed_anyway,
                **_make_elicitors(ctx),
                progress=_make_progress(ctx),
                jira_content_json=jira_content_json,
                source_plan=source_plan,
                attached_image_count=attached_image_count,
                capture_ids=list(capture_ids or []),
                image_gate_ack=image_gate_ack,
                image_carry_ack=image_carry_ack,
            ),
        )

    @mcp.tool()
    async def qa_prepare_test_cases(
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
        source_plan: str = "",
        attached_image_count: int = 0,
        capture_ids: list[str] | None = None,
        image_gate_ack: bool = False,
        image_carry_ack: bool = False,
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
        feature_or_url plus `jira_content_json` set to the raw result as a
        JSON STRING -- stringified JSON, i.e. `json.dumps(result)`, because
        that parameter is typed `str`; do not pass the object itself. Do
        not summarise, translate or invent ticket content, and do not generate
        from the URL alone. If you have no `atlassian` MCP server connected, show
        the user the connection steps the directive includes.

        WHAT TO DO WITH THE RESULT: when the payload includes `orchestration`
        (mode `staged_categories`), generate the categories yourself and stage
        each one with `qa_submit_category` AS SOON AS it is written (Path A),
        then `qa_prep_status` until ready and `qa_submit_suite` with an empty
        suite_json or the review sidecar. Path A is recommended for two reasons,
        neither of which is speed: staged categories survive a chat reload, and
        the server's duplicate prescreen runs only over a staged set. Path B
        (merge everything, one `qa_submit_suite` call) is supported for a client
        that cannot hold a multi-call session, but nothing is saved until that
        single call. Without orchestration, generate the full merged suite
        yourself and call `qa_submit_suite`. The server
        validates, de-duplicates, scores, exports and persists it, and replies
        with the finished suite + file path OR gaps to regenerate under the SAME
        prep_id. Use `qa_get_category_job` with category_name="all" for every
        category packet in ONE call (or one name for a single packet).

        IMAGE GATE (always on). ASK FIRST: for a Jira
        URL, ask the USER where the ticket's SCREENS come from BEFORE your first
        call and pass `source_plan` on it -- this server cannot read images out of
        Jira, only text, so it has to know, and asking up front costs ZERO extra
        tool calls. Only pass `source_plan` if the user ANSWERED -- never guess it,
        and never send `image_gate_ack=true` unless the user explicitly said the
        screens do not matter: that pair skips BOTH asks, including the informed
        one that names the screens the fetched ticket really has, which makes the
        gate quieter rather than cheaper. If you call without a plan, the FIRST
        reply is ONLY that question (nothing is fetched and nothing is prepared)
        and you must call again with
        the SAME feature_or_url plus `source_plan` (`jira` =
        ticket text only, `jira_attach`, `jira_device`, `jira_both`, `device`).
        For `jira_attach` also pass `attached_image_count` = how many images the
        user attached to THIS chat (the bytes stay with you; the payload then
        asks you to describe them and return `image_descriptions`). For
        `jira_device` call `qa_capture_screens` first and pass its `capture_ids`
        -- many screens are fine, and the ids stay valid across the Jira fetch
        directive and any failed attempt, so re-send them unchanged. Once the
        ticket is fetched a SECOND short reply may NAME the screens the ticket
        actually has and ask again; supply them, or pass `image_gate_ack=true` to
        generate from the ticket text anyway. If the user already said the
        screens do not matter, send `source_plan='jira'` AND
        `image_gate_ack=true` together and neither ask appears.

        RE-PREPARING THE SAME SOURCE: if a recent preparation for this source was
        grounded on screens and your new call carries none, this server either
        CARRIES THEM FORWARD (device captures it still holds -- re-sending the
        same `capture_ids` also still works) or REFUSES and names them.
        `proceed_anyway=true` does NOT dismiss that refusal -- either re-send the
        screens (`qa_capture_screens` again, or re-attach them with
        `attached_image_count`), or pass `image_carry_ack=true` once the user has
        agreed to generate without them.

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
                **_make_elicitors(ctx),
                progress=_make_progress(ctx),
                jira_content_json=jira_content_json,
                source_plan=source_plan,
                attached_image_count=attached_image_count,
                capture_ids=list(capture_ids or []),
                image_gate_ack=image_gate_ack,
                image_carry_ack=image_carry_ack,
            ),
        )
        return _prepare_payload_to_content(result)

    @mcp.tool()
    async def qa_submit_suite(
        ctx: Context,
        prep_id: str = "",
        suite_json: str | dict = "",
        volume_floor_ack: bool = False,
        image_relevance_ack: bool = False,
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

        If the reply refuses the submission for being below the per-category
        volume this prep's payload asked for, generate the missing cases and
        resubmit the COMPLETE suite under the same prep_id. `volume_floor_ack`
        is IGNORED on the first submit by design: it only works after that
        refusal, and only the USER may decide it -- show them the numbers and
        pass it on the retry if they confirm, never on your own judgement.

        If the reply refuses the submission because an attached screen was
        judged `relevant: "no"` (or no verdict came back at all), capture or
        attach the correct screen and prepare again, or resubmit the same suite
        with the per-image verdicts filled in. `image_relevance_ack` follows the
        SAME two-beat rule as `volume_floor_ack`: ignored on the first submit,
        honoured only after that refusal, and only ever on the USER's word.
        """
        return await _tracked(
            "qa_submit_suite",
            ctx,
            mcp_handlers.handle_submit_suite(
                prep_id,
                suite_json,
                volume_floor_ack=volume_floor_ack,
                image_relevance_ack=image_relevance_ack,
                ask_text=_make_asker(ctx),
                progress=_make_progress(ctx),
            ),
        )

    @mcp.tool()
    async def qa_prep_status(ctx: Context, prep_id: str = "") -> str:
        """Show which categories are staged for a host-mode prep_id and whether
        Path A (empty suite_json) finalize is allowed yet.

        Use while staging categories with qa_submit_category. ready=yes means you
        may call qa_submit_suite with suite_json="". Path B (full merged
        suite_json) does not require ready=yes.
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

        Use to fetch one category's packet without re-parsing the full prepare
        payload. category_name should match orchestration.expected_categories.
        Pass category_name="all" (or "*") to get EVERY job in ONE call with
        the shared prompt blocks hoisted once -- always preferred; never fetch
        packets one call per category.
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
        replace_smaller: bool = False,
    ) -> str:
        """Submit ONE category's cases for a host that generates incrementally.

        Use this for Path A, the recommended route -- stage each category as soon
        as you finish it: pass the `prep_id` from qa_prepare_test_cases,
        the category name (canonical or known alias), and `suite_json` for THAT
        category -- as a JSON OBJECT when your client can send one, which avoids
        serialising a large payload into a string argument; a JSON string is still
        accepted unchanged. Names are normalized server-side. Re-submitting REPLACES that
        category (newest wins) and the reply SAYS SO -- do NOT re-submit a
        category that is already staged unless a reply asked you to; check
        `qa_prep_status` first. A re-submission carrying FEWER cases than the
        staged row is REFUSED (nothing is saved, the staged row survives) because
        that is usually a truncated output; pass `replace_smaller=true`
        only when dropping those cases is deliberate -- it is always reported.
        When every expected category is staged, call
        qa_submit_suite with the same prep_id and an EMPTY suite_json -- or with
        a small review SIDECAR, which is how the duplicate review rides this
        route (it works on EITHER route; what it needs is the field, not a
        particular route). Check progress with qa_prep_status.
        """
        return await _tracked(
            "qa_submit_category",
            ctx,
            mcp_handlers.handle_submit_category(
                prep_id,
                category_name,
                suite_json,
                progress=_make_progress(ctx),
                replace_smaller=replace_smaller,
            ),
        )

    @mcp.tool()
    async def qa_export_suite(
        ctx: Context, suite_id: str = "", format: str = "", output_dir: str = ""
    ) -> str:
        """Export a previously generated suite (by suite_id) to one of:
        csv | xlsx | gherkin | playwright | testrail.
        Returns the written file path. Reuses the stored suite; live-push dry-run
        defaults are preserved (this writes files, it never pushes to a TMS).

        `output_dir` is OPTIONAL and is where the tester wants the file: pass a
        FULL path (`~/Desktop`, `/Users/you/Documents`). A bare relative answer
        like `desktop` is refused with the full path it probably meant, and the
        configured default is used instead. Leave it empty and each format keeps
        its own default location -- a secure temp folder, which since the
        Zephyr pair was deleted on 2026-08-15 is every format there is.
        The .xlsx that generation auto-exports is unaffected: it always
        lands in QA_EXPORT_DIR with no question asked.
        """
        return await _tracked(
            "qa_export_suite",
            ctx,
            mcp_handlers.handle_export_suite(
                suite_id,
                format,
                output_dir=output_dir,
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

        if settings.qa_testrail_push_enabled or settings.qa_xray_push_enabled:

            @mcp.tool()
            async def qa_push_suite(
                suite_id: str,
                target: str,
                project_id: int = 0,
                section_name: str = "",
                apply: bool = False,
                ctx: Context = None,
            ) -> str:
                """Push a stored test suite into TestRail or Xray.

                target="testrail" (needs the numeric project_id from the TestRail
                URL) or target="xray". Defaults to a PREVIEW: it reports what would
                be created and sends nothing. A real push needs apply=true AND the
                target's kill-switch flag enabled in .env. Nothing here can delete
                the cases afterwards.
                """
                return await _tracked(
                    "qa_push_suite",
                    ctx,
                    mcp_handlers.handle_push_suite(
                        suite_id,
                        target,
                        project_id=project_id,
                        section_name=section_name,
                        apply=apply,
                        progress=_make_progress(ctx),
                    ),
                )

        if settings.qa_api_test_enabled:

            @mcp.tool()
            async def qa_api_project(
                create: str = "", use: str = "", ctx: Context = None
            ) -> str:
                """Create a new API test project, or continue with an existing one.

                Every API flow starts here. Call with NO arguments to get the
                choice plus the projects already registered, and ask the tester in
                plain chat. create="<name>" fetches the public project template,
                renames it to that project, makes ONE local commit (no remote,
                nothing pushed) and proves it compiles before keeping it.
                use="<name or path>" continues with an existing project; an
                existing api-automation-framework checkout is adopted in place.
                Then call qa_prepare_api_tests.
                """
                return await _tracked(
                    "qa_api_project",
                    ctx,
                    mcp_handlers.handle_api_project(
                        create, use, progress=_make_progress(ctx)
                    ),
                )

            @mcp.tool()
            async def qa_prepare_api_tests(
                input: str = "",
                intake_id: str = "",
                confirmed: bool = False,
                project: str = "",
                ctx: Context = None,
            ) -> str:
                """Start (or continue) an API endpoint test intake.

                Chat-only: the server makes NO model call. Paste a filled/partial
                contract template, a curl command, an OpenAPI URL/JSON, or prose.
                Returns an intake card (with the questions to ask) or, once complete
                and confirmed=true, a generation task envelope YOU answer, then call
                qa_submit_api_tests with the task_id and your cases.

                project="<name>" scopes the endpoint and auth-flow registry, so a
                dependency you already built is reused instead of rebuilt. Pass it
                once, on the first call; it is remembered for the rest of the
                intake. Omit it and everything still works for this session only.
                """
                return await _tracked(
                    "qa_prepare_api_tests",
                    ctx,
                    mcp_handlers.handle_prepare_api_tests(
                        input,
                        intake_id,
                        confirmed,
                        project,
                        progress=_make_progress(ctx),
                    ),
                )

            @mcp.tool()
            async def qa_submit_api_tests(
                task_id: str, suite: str, ctx: Context
            ) -> str:
                """Submit the API test cases YOU generated for a qa_prepare_api_tests task.

                Send the task_id and your JSON {"cases": [...]}. The server grounds
                every assertion against the confirmed contract (dropping hallucinated
                fields, refusing cases that cannot fail) and returns the grounded
                suite + a suite_id for qa_write_api_test.
                """
                return await _tracked(
                    "qa_submit_api_tests",
                    ctx,
                    mcp_handlers.handle_submit_api_tests(
                        task_id, suite, progress=_make_progress(ctx)
                    ),
                )

            @mcp.tool()
            async def qa_write_api_test(
                suite_id: str,
                apply: bool = False,
                project: str = "",
                ctx: Context = None,
            ) -> str:
                """Render + (dry-run or) write the Java tests for a finalized suite.

                apply=false (default) returns the branch, target paths and the full
                Java source — nothing is written. apply=true writes via the framework
                repo's own ops pipeline (branch -> write -> spotless -> test-compile
                -> commit), and only when QA_API_FRAMEWORK_WRITE_ENABLED is on and
                QA_API_FRAMEWORK_WRITE_DRY_RUN is off. Never main, never push.

                project="<name>" targets a project registered by qa_api_project;
                omit it to use QA_API_FRAMEWORK_PATH.
                """
                return await _tracked(
                    "qa_write_api_test",
                    ctx,
                    mcp_handlers.handle_write_api_test(
                        suite_id, apply, project, progress=_make_progress(ctx)
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
        """Search the RAG corpus for similar past test cases or bug reports. entry_type is 'test_case' or 'bug_report'; pass
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

    # Registered UNCONDITIONALLY (not inside the full-edition block below):
    # tools/device_manager IS shipped in the test-cases-only edition, capturing
    # app screens GROUNDS test-case generation -- that edition's one job -- and
    # this tool makes no server-side vision call and needs no credentials, so
    # the credential-free promise holds. QA_MOBILE_CAPTURE was DELETED on
    # 2026-08-13 (flag-surface reduction, batch 7) and hardcoded to the `true`
    # the dist .env.example already shipped, so this tool is LIVE on every
    # edition -- a deliberate decision, documented in docs/FEATURE_FLAGS.md and
    # in the dist README's tool table. The handler's gate is now the named seam
    # tools/mcp_handlers._mobile_capture().
    @mcp.tool()
    async def qa_capture_screens(
        ctx: Context,
        device_id: str = "",
        count: int = 1,
        rescan: bool = False,
        names: str = "",
    ) -> list[ContentBlock]:
        """Capture screenshots from a connected phone / emulator / simulator and
        return them as image content PLUS one capture_id per screen.

        Use this when the user wants test cases grounded in the REAL screens --
        especially for a Jira ticket, because this server cannot read images out
        of Jira (the Atlassian MCP connection returns attachment metadata, never
        image bytes). Capture as many screens as you need with `count`, then call
        `qa_prepare_test_cases` (or `qa_generate_test_cases`) with the returned
        `capture_ids` so the generated cases can reference each screen BY NAME.

        Screens are named automatically -- from the ticket's own image labels when
        a prepare disclosed them, else screen_1..N. Pass `names` (comma-separated,
        in capture order, e.g. "Login screen, OTP screen") ONLY if the user told you
        what to call them. Never ask them a separate question about it.

        Omit device_id to get a device picker (it includes a Rescan option for a
        phone plugged in after the list was built); pass rescan=true to force a
        fresh scan. The capture_ids stay valid until a preparation actually uses
        them, and expire after 30 minutes.
        """
        from mcp.types import TextContent

        text, specs = await _tracked(
            "qa_capture_screens",
            ctx,
            mcp_handlers.handle_capture_screens(
                device_id=device_id,
                count=count,
                rescan=rescan,
                names=names,
                **_make_elicitors(ctx),
                progress=_make_progress(ctx),
            ),
        )
        return [TextContent(type="text", text=text), *_image_content_blocks(specs)]

    # Full edition only -- the multi-workflow wizard references modules the
    # distribution build does not ship. Two tool pairs that stood here were
    # DELETED on 2026-08-15: `qa_run_mobile_suite` in dead-code deletion
    # batch D2 with tools/maestro_*.py, and `qa_run_web_suite` /
    # `qa_submit_web_run` in batch D3 with tools/web_runner.py. Both had
    # refused on every install since batches 6/7 retired their features, and
    # a registered tool that can only refuse costs a tester a round trip to
    # learn nothing; a client with either name cached now gets an
    # unknown-tool error instead of a disabled notice, which belongs in the
    # release note. Device capture is unaffected -- qa_capture_screens and
    # qa_list_devices are registered above and still live.
    if not mcp_handlers._test_cases_only():

        @mcp.tool()
        async def qa_wizard(ctx: Context) -> str:
            """Guided entry point for testers: pick a workflow (Test cases / Bug
            report / Exploratory) and I walk you through it
            END-TO-END. Test cases asks where the feature comes from (describe it /
            Jira ticket / mobile screens / Jira + mobile), captures device screens
            when relevant, and returns the generated suite plus the Feature
            Analysis report. No tool names or parameters needed. On clients
            without MCP elicitation it returns a concise markdown menu instead."""
            return await _tracked(
                "qa_wizard",
                ctx,
                mcp_handlers.handle_wizard(
                    **_make_elicitors(ctx),
                    progress=_make_progress(ctx),
                ),
            )

    @mcp.tool(name="qa-doctor")
    async def qa_doctor(ctx: Context) -> str:
        """Check whether THIS machine is ready: overall verdict, LLM backend
        auth, integrations, CLI tooling (adb/xcrun), enabled features and
        action items. Fast and read-only. Run this first on a new machine."""
        progress = _make_progress(ctx)
        # Resolved BEFORE entering _tracked: this is a round trip back to the
        # client, not part of the report's own work, and _tracked owns the
        # in-flight counter that gates the drift restart.
        roots = await _workspace_roots(ctx)
        return await _tracked(
            "qa-doctor",
            ctx,
            mcp_handlers.handle_setup_check(progress=progress, workspace_roots=roots),
        )

    # Optional tool — only in the FULL edition, and only when the Feature
    # Analysis feature is on. 2026-08-03: the public qa-agent-pro build is
    # deliberately test-cases-only AND credential-free, and this PAIR was the
    # last tester-facing path there that could reach a server-side LLM backend
    # (its `mobile` / `jira_mobile` modes describe captured screens through
    # this server's own ask_vision, tools/image_description.py). The edition
    # EDITION gate is what protects the dist, and it still does: the flag was
    # DELETED on 2026-08-14 (batch 8c) and hardcoded ON, so _test_cases_only()
    # is now the ONLY thing standing between the public build and this pair.
    # (Since 2026-08-15 the mobile modes reach NO ask_vision: the captured
    # screens are attached to the reply as MCP image content for the tester's
    # own model.) It is checked here and again inside both handlers,
    # deliberately.
    if _feature_analysis_enabled() and not mcp_handlers._test_cases_only():

        @mcp.tool()
        async def qa_feature_analysis(
            ctx: Context,
            feature_or_url: str = "",
            mode: str = "",
            device_id: str = "",
            jira_content_json: str = "",
        ) -> list[ContentBlock]:
            """Start a compact enterprise Feature Analysis Report.

            Chat-only: the server makes NO model call. It returns a task envelope
            (system_prompt + untrusted-wrapped context + a response_schema) that
            YOU answer, then you call qa_submit_feature_analysis with the task_id
            and your JSON report.

            mode is one of: jira (analyse a feature description or Jira/issue
            URL), mobile (capture screens from a connected device), or
            jira_mobile (merge the ticket with captured screens). Omit mode and I'll ask; the mobile modes also ask for the
            device and offer a capture-another-screen loop, and the captured
            screens are attached to the reply as images for YOUR model to read
            -- this server makes no vision call.

            For a Jira URL the reply may be a DIRECTIVE asking you to fetch the
            issue with your own mcp__atlassian__getJiraIssue tool and call again
            with jira_content_json set to its raw result as a JSON STRING
            (stringified JSON -- that parameter is typed `str`)."""
            from mcp.types import TextContent

            reply = await _tracked(
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
            # Captured screens ride to the tester's own multimodal model as
            # image content. getattr: every other return path is a plain str.
            return [
                TextContent(type="text", text=str(reply)),
                *_image_content_blocks(getattr(reply, "images", ())),
            ]

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

    _register_prompts(
        mcp,
        test_cases_only=edition_test_cases_only,
        api_tests=edition_api_tests,
    )

    return mcp


def _configure_logging() -> None:
    """INFO+ to THIS PROCESS's own file under data/logs/; WARNING+ to stderr.

    Over stdio, MCP clients render EVERY stderr line as an error (Cursor logs
    "[error] INFO ..." for each httpx/telemetry line), which buries real
    failures in noise. Errors stay on stderr; the full INFO trail moves to a
    file an operator can tail. Never raises -- if the file handler cannot be
    created, stderr keeps INFO so nothing is lost.

    2026-08-09: the file is PER-PROCESS (``qa-agents-<pid>.log``). One install
    is shared by up to three MCP clients, and a shared RotatingFileHandler had
    three processes rotating one name -- each rollover stranded the other two on
    a rotated-away inode, so a 15:08 finalize wrote its audit rows and its xlsx
    while the log's entries stopped at 15:04. Every line now carries the pid and
    (once the initialize handshake has happened) the client name, so a diagnoser
    can attribute it. See tools/log_setup.py for the full mechanism."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    root.addHandler(stderr_handler)
    log_dir = Path(__file__).resolve().parent / "data" / "logs"
    handler = None
    try:
        # The IMPORT is inside the guard on purpose: this function is the first
        # statement of main(), so a whitelist regression in the dist build or a
        # shadowed module would otherwise kill the server before a single line
        # could say why -- which is exactly the class of silence being fixed.
        from tools.log_setup import configure_file_logging, process_log_path

        handler = configure_file_logging(log_dir)
    except Exception:
        handler = None
    if handler is None:
        stderr_handler.setLevel(logging.INFO)
        logger.warning(
            "Could not open a log file under data/logs -- keeping INFO on stderr."
        )
    else:
        logger.info("logging to %s", process_log_path(log_dir))
    # Third-party request logging is diagnostic noise at INFO (one line per
    # telemetry POST); real problems still surface at WARNING+. FastMCP is in
    # the list because it attaches its OWN rich handler (bypassing the root
    # config above), so its INFO transport banner still reached stderr on the
    # v1.38.0 validation run.
    #
    # ``mcp.server.lowlevel.server`` joined the list on 2026-08-19 (F07): its
    # INFO line is "Processing request of type CallToolRequest" and names
    # nothing, so it cost one line per call and told a diagnoser nothing.
    # ``_tracked`` now logs a NAMED line for every call, which is what that line
    # was standing in for -- dropping it keeps per-call volume flat.
    for noisy in ("httpx", "httpcore", "FastMCP", "mcp.server.lowlevel.server"):
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

    # _prewarm_backend stood here until 2026-08-16 (dead-code deletion P2-G2b).
    # It was a daemon thread calling llm._cursor_usable() to warm the
    # cursor-agent auth probe -- up to 20s -- off the serving path. P2-G2c
    # deletes all three backends, so there is no probe to warm; the bare
    # `except` around it is exactly why this had to be deleted deliberately
    # rather than left to fail silently.
    threading.Thread(target=_drift_watch, daemon=True).start()
    logger.info("Starting the qa-agents MCP server over stdio…")
    server.run(show_banner=False)


if __name__ == "__main__":
    main()

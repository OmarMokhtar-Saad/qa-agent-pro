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
from tools import dispatch_guard  # noqa: E402
from tools import guidance  # noqa: E402
from tools import mcp_handlers  # noqa: E402
from tools import telemetry  # noqa: E402
from tools import tool_briefs  # noqa: E402
from tools import selfcheck as selfcheck_module  # noqa: E402

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
        # v1.97.0 cursor-hardening (item 6a): forward clientInfo a second
        # time so tools.mcp_handlers._dispatch_provenance can stamp it onto
        # every saved suite -- both already-set values at this point
        # (_CLIENT["name"]/["version"] just above).
        mcp_handlers.note_provenance_client(name, _CLIENT.get("version", ""))
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

# P1-5 (Air onboarding fix): a duplicate call landing within this window --
# a host retry, a double-click in a chat UI -- returns the cached result
# instead of re-running the tool. Deliberately NOT inside `_tracked()`: that
# function's docstring says it is structurally denied an `args` parameter so
# a payload can never leak through the one seam every tool shares, so this
# cache has to live at each call site instead.
_RECENT_CALL_TTL_S = 5.0
# qa_generate_test_cases gets a longer window: in the Air run the duplicate
# arrived ~6s after a 17s call had FINISHED, which 5s would miss. This is a
# duplicate-call guard, NOT the removed 24h finished-suite stop: 30s, same
# args only, and only a reply that created a prep is replayed.
_GENERATE_DUP_WINDOW_S = 30.0
_RECENT_CALLS: dict = {}
_RECENT_CALLS_LOCK = threading.Lock()


def _recent_call_cached(key, ttl_s: float = _RECENT_CALL_TTL_S):
    """Return the cached result for key if still within ttl_s, else None.
    Never raises."""
    with _RECENT_CALLS_LOCK:
        entry = _RECENT_CALLS.get(key)
        if not entry:
            return None
        result, at = entry
        if time.monotonic() - at > ttl_s:
            _RECENT_CALLS.pop(key, None)
            return None
        return result


def _recent_call_store(key, result) -> None:
    """Store result under key with the current time, for _recent_call_cached
    to serve back within the TTL. Drops every entry older than the longest
    window first, so the cache cannot grow for the life of the process."""
    now = time.monotonic()
    horizon = max(_RECENT_CALL_TTL_S, _GENERATE_DUP_WINDOW_S)
    with _RECENT_CALLS_LOCK:
        for stale in [k for k, (_, at) in _RECENT_CALLS.items() if now - at > horizon]:
            del _RECENT_CALLS[stale]
        _RECENT_CALLS[key] = (result, now)


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
    except (TypeError, ValueError, OverflowError):
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


def _device_ref(device_id: str = "", serial: str = "") -> str:
    """ONE device reference from the two names a caller may use.

    ``device_id`` is the canonical parameter on every device-taking tool and
    ``serial`` is accepted as an alias for the same value (a model that read
    one tool's schema guessed the other's name). Both given and different is a
    contradiction the server will not resolve by guessing which phone to drive,
    so it raises; FastMCP returns the message to the caller as the tool error.
    """
    first = str(device_id or "").strip()
    second = str(serial or "").strip()
    if first and second and first != second:
        raise ValueError(
            "device_id and serial name different devices (device_id=%r, "
            "serial=%r). Pass ONE of them: serial is an alias for device_id."
            % (first, second)
        )
    return first or second


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
    # The client identity is read HERE, before the handler runs, so every tool
    # (qa_host_check included) logs and tags its real editor. It used to be read
    # only from `_make_progress`, which tools that report no progress never
    # reached, leaving `client` unknown. Never raises (see `_note_client`).
    _note_client(ctx)
    start = time.monotonic()
    ok = True
    error_type = None
    # A per-call sink for facts a returned handler must not report as `ok`
    # -- an adb screencap or dump that timed out (fix round 3, item 3).
    from tools import tool_status

    _status_token = tool_status.begin()
    telemetry.start_tool_trace(name)
    _inflight_enter()
    _dispatch_token = dispatch_guard.enter_dispatched()
    try:
        return await coro
    except Exception as exc:
        ok = False
        error_type = type(exc).__name__
        telemetry.capture_error_dist(exc, tool=name, origin="mcp_tool")
        raise
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        # A handler that RETURNED after a device timeout is not `ok`: the Air
        # run logged "tool qa_mobile_test: ok in 47589 ms" over a screencap
        # that had timed out after 30 s (fix round 3, item 3). A raised
        # exception keeps its own error type.
        timed_out = tool_status.finish(_status_token)
        degraded = ok and bool(timed_out)
        if degraded:
            ok = False
            error_type = ("timed_out:" + ",".join(sorted(set(timed_out))))[:120]
        logger.info(
            "tool %s: %s in %d ms",
            name,
            "ok"
            if ok
            else (error_type if degraded else "error %s" % (error_type or "Exception")),
            duration_ms,
        )
        try:
            from tools import audit_log

            await audit_log.record_event(
                event_type="tool_called",
                actor=_CLIENT.get("name", "") or None,
                detail={
                    "tool": name,
                    "duration_ms": duration_ms,
                    "ok": ok,
                    "error_type": error_type,
                    "status": ("timed_out" if degraded else ("ok" if ok else "error")),
                },
            )
        except Exception:
            logger.debug("audit_log record_event failed", exc_info=True)
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
        dispatch_guard.exit_dispatched(_dispatch_token)
        _inflight_exit()


# A progress ping is a NOTIFICATION: the tool does not need its result and
# must never wait on it. Bounded well below the client-side tool timeout the
# ping exists to reset, so a stalled client costs one dropped ping rather
# than the call. Same reasoning and same order of magnitude as
# _ROOTS_TIMEOUT_S; kept separate because they answer to different clients'
# capabilities and should be tunable apart.
async def _tracked_noted(name, ctx, coro):
    """``_tracked`` plus the tool's own note (``tools/tool_briefs.TOOL_NOTES``)
    appended to a text reply. ``_tracked`` itself stays a pure pass-through."""
    return tool_briefs.append_tool_note(name, await _tracked(name, ctx, coro))


_PROGRESS_TIMEOUT_S = 5.0


def _make_progress(ctx):
    """Adapt a FastMCP Context into the handlers' ``(message)->awaitable`` callback.

    Every call reports incremental progress so a long-running generation or
    device run resets the MCP client's tool-call timeout (progress notifications
    keep the stream alive). Best-effort — a transport hiccup never breaks the
    tool.

    2026-09-01: "never breaks the tool" was true only for a client that
    RAISES. A client that accepts the notification and never completes the
    await is not an exception, and the ``except Exception`` below cannot see
    it -- the tool call simply hung, forever, on a best-effort progress ping.
    Every other client round trip in this file is already bounded
    (:func:`_workspace_roots` by ``_ROOTS_TIMEOUT_S``, both elicitation tiers
    by ``asyncio.wait_for``); this was the one that was not, and it is the
    most frequent of them by a wide margin. The timeout feeds the SAME
    ``except`` clause -- ``asyncio.TimeoutError`` is an ``Exception`` on every
    version this project supports -- so a slow client degrades to exactly the
    behaviour a raising one always had: the ping is dropped, logged at DEBUG,
    and the tool continues.
    """
    _note_client(ctx)  # every tool builds one of these — cheap host detection
    state = {"n": 0}

    async def progress(message: str) -> None:
        state["n"] += 1
        try:
            await asyncio.wait_for(
                ctx.report_progress(progress=state["n"], total=None, message=message),
                timeout=_PROGRESS_TIMEOUT_S,
            )
        except Exception:
            logger.debug("mcp report_progress failed for %r", message, exc_info=True)

    # S16: a long mobile wait (boot, a loading screen) reports through the same
    # channel. Bound per call context; the mobile modules may be absent.
    try:
        from tools.mobile import watch

        watch.bind_progress(progress)
    except ImportError:
        pass
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


def _register_prompts(
    mcp, *, test_cases_only: bool, api_tests: bool, mobile: bool = False
) -> None:
    """Register one MCP Prompt per guidance workflow this edition can support.

    A loop rather than N decorated functions on purpose: the gate deciding
    WHICH prompts exist already lives in ``tools/guidance.prompt_texts``, and
    re-expressing it here as a second ladder of ``if`` statements is exactly how
    two copies of one rule drift apart. Each body is closed over through a
    factory -- closing over the loop variable directly would hand every prompt
    the LAST body, silently.
    """
    for name, body in guidance.prompt_texts(
        test_cases_only=test_cases_only, api_tests=api_tests, mobile=mobile
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


def _with_script_doc(fn):
    """Append the step-script format (``script_help.SCRIPT_FORMAT_DOC``) to a
    tool's description, so the model reads it before it writes a script."""
    from tools.mobile import script_help

    fn.__doc__ = (fn.__doc__ or "") + "\n\n" + script_help.SCRIPT_FORMAT_DOC
    return fn


def _register_mobile_defaults() -> None:
    """Register the mobile lane's default dialog handlers, install sources and
    tree sources once at startup. Every register call is idempotent; a failure
    is logged and leaves the rest of the server up."""
    if not mcp_handlers._mobile_lane_enabled():
        return
    try:
        from tools.mobile import (
            dialog_defaults,
            dump_tree_source,
            install_sources,
            u2_tree_source,
        )

        dialog_defaults.register_default_handlers()
        install_sources.register_defaults()
        dump_tree_source.register_dump_source()
        u2_tree_source.register_u2_source()
    except (ImportError, OSError, RuntimeError, ValueError):
        logger.warning("mobile default registration failed", exc_info=True)


def build_server():
    """Construct and return the FastMCP server with every qa_* tool registered.

    ``fastmcp`` is imported here (not at module top level) so importing this
    module never requires the optional extra — only actually starting the server
    does.
    """
    from fastmcp import Context, FastMCP
    from mcp.types import ContentBlock, ToolAnnotations

    # The two edition gates, read here so the GUIDANCE text is built from the
    # same expressions the registration gates below use. (Those gates still
    # call `_test_cases_only()` inline at their own sites; this is a second
    # read of one pure function, not a second source of truth.) The guidance
    # names tools, and a client that calls a tool this edition never registered
    # fails mid-workflow in front of a tester -- so the text and the
    # registration must not be able to disagree.
    edition_test_cases_only = mcp_handlers._test_cases_only()
    edition_api_tests = bool(
        settings.qa_api_test_enabled
    )  # The mobile lane's gate is a single named predicate rather than an
    # expression, and it is read here for the same reason as the two above: the
    # guidance names three tools and must not be able to name them on an
    # edition that does not register them. Its own definition is the
    # whole gate (the modules on disk), so nothing at the
    # registration site below may add a second term -- a test asserts that site
    # holds nothing but a call to this.
    edition_mobile = mcp_handlers._mobile_lane_enabled()

    # P1-4 (Air onboarding fix): a drift restart silently swaps the process
    # under a tester mid-session; the reload marker was previously read only
    # by qa-doctor's next call. Peeking at it here too -- in the
    # `instructions=` block every client reads at `initialize`, before any
    # tool call -- reaches the tester's actual next action. PEEK, never
    # consume: the marker is one-shot and qa-doctor's reload-outcome report
    # must still find it. Read-only report,
    # never a `_tracked()` side effect: never raises, never blocks startup.
    reload_notice = ""
    try:
        from tools.mcp_handlers import _peek_reload_marker

        marker = _peek_reload_marker() or {}
        if marker.get("reason"):
            reload_notice = (
                "\n\nNOTE: this server restarted mid-session ("
                f"{marker.get('reason')}) to pick up a change. If a reply "
                "looks stale, say so and I will retry."
            )
    except Exception:
        reload_notice = ""

    mcp = FastMCP(
        SERVER_NAME,
        instructions=guidance.server_instructions(
            test_cases_only=edition_test_cases_only,
            api_tests=edition_api_tests,
            mobile=edition_mobile,
        )
        + reload_notice,
    )

    # Log the calls FastMCP rejects on argument validation (they never reach
    # `_tracked`). Guarded twice: the test doubles lack `add_middleware`, and the
    # pinned fastmcp (3.4.7) is unverified against the installed 2.14.7.
    try:
        if hasattr(mcp, "add_middleware"):
            from tools import mcp_rejection_log

            _rejection_log = mcp_rejection_log.build_middleware()
            if _rejection_log is not None:
                mcp.add_middleware(_rejection_log)
    except Exception:
        logger.debug("rejection-log middleware not installed", exc_info=True)

    @mcp.tool()
    async def qa_generate_test_cases(
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
        stage_token: str = "",
        source_plan: str = "",
        attached_image_count: int = 0,
        capture_ids: list[str] | None = None,
        image_gate_ack: bool = False,
        image_carry_ack: bool = False,
    ) -> str:
        """Generate a structured test suite. feature_or_url: a description, Jira/issue URL, web page URL or Swagger/OpenAPI URL; omit it if the user did not say, the server asks.

        Returns a PREPARE PAYLOAD: YOU write the cases. Continue exactly as for qa_prepare_test_cases (qa_get_category_job(prep_id, "all") once, qa_submit_category per category, qa_submit_suite to finalize). Relay the .xlsx path from that reply; do NOT ask which export format or offer to push.

        Jira URL: a DIRECTIVE first; fetch with your own mcp__atlassian__getJiraIssue, `qa_stage_jira` each result, call again with the SAME `stage_token` (or `jira_content_json` as a JSON STRING).

        IMAGE GATE. ASK FIRST: for a Jira URL, ask the USER where the ticket's screens come from before your first call and pass `source_plan`; never guess it, and never send image_gate_ack=true unless the user explicitly said the screens do not matter."""
        _cache_key = (
            "qa_generate_test_cases",
            feature_or_url,
            proceed_anyway,
            jira_content_json,
            source_plan,
            attached_image_count,
            tuple(capture_ids or ()),
            image_gate_ack,
            image_carry_ack,
        )
        _cached = _recent_call_cached(_cache_key, _GENERATE_DUP_WINDOW_S)
        if _cached is not None:
            return (
                "Already done: an identical `qa_generate_test_cases` call "
                "finished moments ago, so nothing was re-run. Its reply "
                "follows; continue from its prep_id.\n\n" + _cached
            )
        _result = await _tracked(
            "qa_generate_test_cases",
            ctx,
            mcp_handlers.handle_generate_test_cases(
                feature_or_url,
                proceed_anyway=proceed_anyway,
                **_make_elicitors(ctx),
                progress=_make_progress(ctx),
                jira_content_json=jira_content_json,
                stage_token=stage_token,
                source_plan=source_plan,
                attached_image_count=attached_image_count,
                capture_ids=list(capture_ids or []),
                image_gate_ack=image_gate_ack,
                image_carry_ack=image_carry_ack,
            ),
        )
        # Only a reply that created a prep: replaying a clarify or a gate
        # block would hide a changed tester answer (e.g. the P0-3 dialog).
        if "**prep_id:** `" in _result:
            _recent_call_store(_cache_key, _result)
        return _result

    @mcp.tool()
    async def qa_stage_jira(
        ctx: Context, stage_token: str, part: str, json: str
    ) -> str:
        """Stage ONE raw Jira fetch result right after you fetch it. Call it after each getJiraIssue/JQL call the qa_prepare_test_cases/qa_generate_test_cases directive asked for: part='issue', 'parent' or 'siblings'. `json` is that ONE result, stringified (json.dumps(result)), under ~20KB. When every part is staged, call qa_prepare_test_cases again with the SAME feature_or_url and `stage_token`, not jira_content_json."""
        return await _tracked(
            "qa_stage_jira",
            ctx,
            mcp_handlers.handle_stage_jira(stage_token, part, json),
        )

    @mcp.tool()
    async def qa_prepare_test_cases(
        ctx: Context,
        feature_or_url: str = "",
        proceed_anyway: bool = False,
        jira_content_json: str = "",
        stage_token: str = "",
        source_plan: str = "",
        attached_image_count: int = 0,
        capture_ids: list[str] | None = None,
        image_gate_ack: bool = False,
        image_carry_ack: bool = False,
    ) -> list[ContentBlock]:
        """HOST-MODE generation: returns a grounded generation payload for YOU, the host model, to run; the server calls no LLM. Call it first for any test-case request.

        feature_or_url: a description, Jira/issue URL, web page URL or Swagger/OpenAPI URL. A Jira URL first returns a DIRECTIVE: fetch with your own `mcp__atlassian__getJiraIssue`, `qa_stage_jira` each raw result, then call again with the SAME feature_or_url and `stage_token` (or `jira_content_json` as a JSON STRING, json.dumps(result)).

        With `orchestration` in the payload: generate each category, stage it with `qa_submit_category`, then `qa_prep_status`, then `qa_submit_suite`. Otherwise generate the full suite and call `qa_submit_suite`.

        IMAGE GATE. ASK FIRST: for a Jira URL, ask the USER where the ticket's screens come from before your first call and pass `source_plan` (jira, jira_attach, jira_device, jira_both, device); never guess it, and never send image_gate_ack=true unless the user explicitly said the screens do not matter.

        Under-specified ticket: relay the questions, or pass proceed_anyway=true. Each prepared (non-clarify) reply carries the standing rules."""
        result = await _tracked(
            "qa_prepare_test_cases",
            ctx,
            mcp_handlers.handle_prepare_test_cases(
                feature_or_url,
                proceed_anyway=proceed_anyway,
                **_make_elicitors(ctx),
                progress=_make_progress(ctx),
                jira_content_json=jira_content_json,
                stage_token=stage_token,
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
        step_assertion_ack: bool = False,
        quality_gate_ack: bool = False,
    ) -> str:
        """Submit a host-generated suite to be validated, finalized, exported and persisted (the BACK half of host mode). Call AFTER qa_prepare_test_cases with its `prep_id` and `suite_json`: one JSON object with a merged `test_cases` array per the payload's response_schema (an object, or a JSON string).

        Path A (after qa_submit_category): pass a small review SIDECAR, a JSON object with `duplicate_groups` (empty list if none) and NO `test_cases`; suite_json="" also finalizes but forfeits this review.

        The reply is the finished suite plus the exported file path (relay it, do not ask which format), OR refusals and gaps: fix just those and call again with the SAME prep_id. The acks (volume_floor_ack, image_relevance_ack, step_assertion_ack, quality_gate_ack) work only after that refusal and only on the USER's word, never your own judgement."""
        return await _tracked(
            "qa_submit_suite",
            ctx,
            mcp_handlers.handle_submit_suite(
                prep_id,
                suite_json,
                volume_floor_ack=volume_floor_ack,
                image_relevance_ack=image_relevance_ack,
                step_assertion_ack=step_assertion_ack,
                quality_gate_ack=quality_gate_ack,
                ask_text=_make_asker(ctx),
                progress=_make_progress(ctx),
            ),
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def qa_prep_status(ctx: Context, prep_id: str = "") -> str:
        """Show which categories are staged for a host-mode prep_id and whether the per-category (Path A) finalize is allowed yet. Use while staging with qa_submit_category. ready=yes means you may call qa_submit_suite with a small review SIDECAR carrying duplicate_groups, or suite_json="" (no review). Path B (full merged suite_json) does not need ready=yes."""
        return await _tracked(
            "qa_prep_status",
            ctx,
            mcp_handlers.handle_prep_status(prep_id),
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def qa_get_category_job(
        ctx: Context, prep_id: str = "", category_name: str = ""
    ) -> str:
        """Return ONE self-contained category generation job for a prep_id (system_prompt + user_context + instruction + response_schema). category_name should match orchestration.expected_categories; pass "all" (or "*") for EVERY job in ONE call, always preferred. Generation runs 6.5-24 minutes per category in your own chat model: tell the tester a short status line -- "Generating <category>..." -- before you start writing each category, so a long gap is never silent."""
        _cache_key = ("qa_get_category_job", prep_id, category_name)
        _cached = _recent_call_cached(_cache_key)
        if _cached is not None:
            return _cached
        _result = await _tracked(
            "qa_get_category_job",
            ctx,
            mcp_handlers.handle_get_category_job(prep_id, category_name),
        )
        _recent_call_store(_cache_key, _result)
        return _result

    @mcp.tool()
    async def qa_submit_category(
        ctx: Context,
        prep_id: str = "",
        category_name: str = "",
        suite_json: str | dict = "",
        replace_smaller: bool = False,
    ) -> str:
        """Submit ONE category's cases (Path A, recommended): stage each category as soon as it is written. Pass the `prep_id` from qa_prepare_test_cases, the category name and `suite_json` for THAT category (a JSON object, or a string). Re-submitting REPLACES that category; do not re-submit a staged one unless a reply asked (check `qa_prep_status`). Fewer cases than staged is REFUSED; replace_smaller=true only when deliberate. When all are staged, call qa_submit_suite with the same prep_id and an empty suite_json or a review SIDECAR."""
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
        ctx: Context,
        suite_id: str = "",
        format: str = "",
        output_dir: str = "",
        prep_id: str = "",
    ) -> str:
        """Export a stored suite (by suite_id) to csv | xlsx | gherkin | playwright | testrail; returns the written file path. Writes files only, never pushes to a TMS. `output_dir` is optional: a FULL path (`~/Desktop`); a bare relative word is refused. `prep_id` is NOT an export key: only the suite_id `qa_submit_suite` returns is."""
        return await _tracked(
            "qa_export_suite",
            ctx,
            mcp_handlers.handle_export_suite(
                suite_id,
                format,
                output_dir=output_dir,
                prep_id=prep_id,
                choose=_make_chooser(ctx),
                progress=_make_progress(ctx),
            ),
        )

    # The mobile emulator lane. ONE call, and nothing else may join it here:
    # `_mobile_lane_enabled()` is `_mobile_modules_present()` alone, which
    # checks tools/mobile is really on disk -- a build made without the pinned
    # IME ships none of it. QA_MOBILE_HTTPS_CAPTURE_ENABLED is not a term: it gates only
    # the capture download and the capture-certificate install, at those effects.
    # tests/mobile/test_mobile_registration.py parses this file and fails if
    # this `if` becomes anything other than a call to that predicate.
    #
    # It carried a THIRD term, `not _test_cases_only()`, until 2026-09-04. That
    # is an edition label decided by whether the bug-report and coach agents
    # shipped, and it made the lane unreachable on every distribution build:
    # v1.77.0 registered none of these three tools while its own README
    # promised all three. Do not re-add it here or in the predicate.
    if mcp_handlers._mobile_lane_enabled():

        @mcp.tool()
        async def qa_mobile_stop(ctx: Context, target: str = "") -> str:
            """Stop ONE mobile run and free its device. `target`: run id or device serial. Do not guess a run or device: ask the user."""
            return await _tracked(
                "qa_mobile_stop",
                ctx,
                mcp_handlers.handle_mobile_stop(target, **_make_elicitors(ctx)),
            )

        @mcp.tool()
        async def qa_app_info(
            ctx: Context, app: str = "", package: str = "", device_id: str = ""
        ) -> str:
            """Read an installed app's version on a device. Touches nothing. Do not guess an app, package or device: ask the user."""
            return await _tracked(
                "qa_app_info",
                ctx,
                mcp_handlers.handle_app_info(
                    app, package, device_id, **_make_elicitors(ctx)
                ),
            )

        @mcp.tool()
        async def qa_update_app(
            ctx: Context,
            app: str = "",
            package: str = "",
            device_id: str = "",
            source: str = "",
        ) -> str:
            """Update an app on a device from an install `source`; a downgrade asks before uninstalling. Do not guess an app, package, device or source: ask the user."""
            return await _tracked(
                "qa_update_app",
                ctx,
                mcp_handlers.handle_update_app(
                    app, package, device_id, source, **_make_elicitors(ctx)
                ),
            )

        @mcp.tool()
        async def qa_mobile_test(
            ctx: Context,
            source: str = "",
            suite_id: str = "",
            goal: str = "",
            cases: str = "",
            app: str = "",
            package: str = "",
            run_id: str = "",
            session_token: str = "",
            apply: bool = False,
            continue_run: bool = False,
            serial: str = "",
            device_id: str = "",
            avd: str = "",
            new_run: bool = False,
            locale: str = "",
            capture: str = "",
            capture_ack: bool = False,
            reset_app: bool = False,
            charter: str = "",
            note: str = "",
            screenshot: bool = False,
            emulator: str = "",
            system_image: str = "",
            confirm_destructive: bool = False,
        ) -> list[ContentBlock]:
            """Drive an Android device: ad-hoc steps (goal=...), cases, exploration.

            ANY Android device action goes through this tool. Do NOT use raw adb or shell: this tool owns the destructive guard, run folder and evidence.

            Call with NO arguments to start. Install/download/launch needs apply=true. run_id continues a run in ANY chat. ONE packet at a time; answer each with qa_submit_mobile_step. Install key in `source`, its value in `app` (or `package`); `device_id`: an adb serial; `avd`: one to boot; `charter`: JSON, no secrets; `locale`, `reset_app=true` need apply=true; new_run=true only if the tester asks. `note`: JSON lesson about THIS app for later runs; plain text, so never a secret.

            Never report a screen state, field value or login outcome that was not read from a qa_* observation. If the server cannot type or act, stop and report the blocker by name. Do not fall back to raw adb input, and do not claim a result. Relay a finished run's verdict block word for word. Ask, never guess, the app, package, device or source."""
            from mcp.types import TextContent

            text, specs = await _tracked(
                "qa_mobile_test",
                ctx,
                mcp_handlers.handle_mobile_test_content(
                    source,
                    suite_id,
                    goal,
                    cases,
                    app,
                    package,
                    run_id,
                    session_token,
                    apply,
                    continue_run,
                    serial=_device_ref(device_id, serial),
                    avd=avd,
                    new_run=new_run,
                    locale=locale,
                    capture=capture,
                    capture_ack=capture_ack,
                    reset_app=reset_app,
                    charter=charter,
                    note=note,
                    screenshot=screenshot,
                    emulator=emulator,
                    system_image=system_image,
                    confirm_destructive=confirm_destructive,
                    **_make_elicitors(ctx),
                    progress=_make_progress(ctx),
                ),
            )
            # The qa_capture_screens shape, deliberately identical: one text
            # block plus one image block per captured screen, with that
            # helper's NEVER-silent text fallback when the fastmcp Image API is
            # unavailable. A packet with no picture yields the text block alone
            # and SAYS so in its own `screen_image` note -- the reply never
            # implies an image that is not there.
            return [
                TextContent(type="text", text=text),
                *_image_content_blocks(specs),
            ]

        @mcp.tool()
        @_with_script_doc
        async def qa_submit_mobile_step(
            run_id: str,
            ctx: Context,
            tc_id: str = "",
            script: str = "",
            tester_input: str = "",
            tester_input_field: str = "",
            session_token: str = "",
            confirm_destructive: bool = False,
            tester_inputs: str = "",
            note: str = "",
            screenshot: bool = False,
            flow: str = "",
            flow_params: str = "",
            save_flow: str = "",
            route: str = "",
            save_route: str = "",
            finding: str = "",
        ) -> list[ContentBlock]:
            """Submit the action script YOU planned for a mobile packet.

            The reply is the verdict plus the NEXT packet. For a credential, ask the TESTER for that field and pass it as tester_input with tester_input_field (several: tester_inputs='{"login_password": "...", "login_otp": "..."}'); it is typed into the app and stored nowhere. Pass confirm_destructive=true only after the TESTER confirms a guard stop.

            `note`: as on `qa_mobile_test`. Never report a screen state, field value or login outcome that was not read from a qa_* observation. If the server cannot type or act, stop and report the blocker by name. Do not fall back to raw adb input, and do not claim a result. `finding`: explore runs only, one observation per turn."""
            from mcp.types import TextContent

            text, specs = await _tracked(
                "qa_submit_mobile_step",
                ctx,
                mcp_handlers.handle_submit_mobile_step_content(
                    run_id,
                    tc_id,
                    script,
                    tester_input,
                    tester_input_field,
                    session_token,
                    confirm_destructive=confirm_destructive,
                    tester_inputs=tester_inputs,
                    note=note,
                    screenshot=screenshot,
                    flow=flow,
                    flow_params=flow_params,
                    save_flow=save_flow,
                    route=route,
                    save_route=save_route,
                    finding=finding,
                    progress=_make_progress(ctx),
                ),
            )
            # Same shape as qa_mobile_test above and as qa_capture_screens.
            return [
                TextContent(type="text", text=text),
                *_image_content_blocks(specs),
            ]

        @mcp.tool()
        async def qa_mobile_status(
            ctx: Context,
            run_id: str = "",
            session_token: str = "",
            report_now: bool = False,
        ) -> str:
            """Where a mobile run stands, read from disk. Touches no device. Device actions go through `qa_mobile_test` (goal=... for ad-hoc steps), never raw adb.

            Emulator, lease holder, cases done/failed/remaining. Call after anything outliving a tool call (big install, cold boot). No run_id lists runs; report_now=true writes the HTML report and returns its path."""
            return await _tracked(
                "qa_mobile_status",
                ctx,
                mcp_handlers.handle_mobile_status(
                    run_id, session_token, report_now, progress=_make_progress(ctx)
                ),
            )

        # Inside the mobile-lane gate, deliberately: a network watch drives the
        # emulator console and samples the device's socket table, so it lives
        # or dies with `_mobile_lane_enabled()` exactly like the three tools
        # beside it. No flag: like the rest of the lane it runs wherever the
        # modules are on disk.
        @mcp.tool()
        async def qa_network_watch(
            ctx: Context,
            action: str = "status",
            serial: str = "",
            package: str = "",
            apply: bool = False,
            device_id: str = "",
        ) -> str:
            """Watch what an emulator puts on the wire, passively, root-free. `device_id`: adb serial (alias `serial`). `action="start"` needs `apply=true`; `"stop"` (REVERTS) never does; `"status"`. Hosts come from TLS ClientHello and DNS; **paths and query strings are not visible and never guessed**. The pcap is parsed and DELETED; only the summary returns."""
            return await _tracked_noted(
                "qa_network_watch",
                ctx,
                mcp_handlers.handle_network_watch(
                    action, _device_ref(device_id, serial), package, apply
                ),
            )

        @mcp.tool()
        async def qa_mobile_notes(
            ctx: Context,
            action: str = "list",
            package: str = "",
            run_id: str = "",
            note_id: int = 0,
            reason: str = "",
        ) -> str:
            """List or retire ONE app's saved notes (by `package` or `run_id`).

            "list": active notes, confirmed/contradicted counts; "retire": `note_id`, kept in history (`reason` holds no secret). Saved via `note` on qa_mobile_test."""
            return await _tracked(
                "qa_mobile_notes",
                ctx,
                mcp_handlers.handle_mobile_notes(
                    action, package, run_id, note_id, reason
                ),
            )

        @mcp.tool()
        async def qa_mobile_flows(
            ctx: Context,
            action: str = "list",
            package: str = "",
            run_id: str = "",
            name: str = "",
            kind: str = "flow",
        ) -> str:
            """List, show or delete one app's saved flows."""
            return await _tracked(
                "qa_mobile_flows",
                ctx,
                mcp_handlers.handle_mobile_flows(action, package, run_id, name, kind),
            )

        @mcp.tool()
        async def qa_setup_capture(
            ctx: Context,
            serial: str = "",
            action: str = "prepare",
            apply: bool = False,
            capture_ack: bool = False,
            device_id: str = "",
        ) -> str:
            """Prepare, check or remove API capture on a device AHEAD of a run. `device_id` is the adb serial (`serial` is an alias). `action="prepare"` installs the qa-agents proxy certificate and proves decryption; `"status"` reads the device's trust from disk; `"remove"` clears the live proxy. prepare and remove need `apply=true`."""
            return await _tracked_noted(
                "qa_setup_capture",
                ctx,
                mcp_handlers.handle_setup_capture(
                    _device_ref(device_id, serial), action, apply, capture_ack
                ),
            )

    # Full edition only — the distribution build exposes test-case tools alone.
    if not mcp_handlers._test_cases_only():

        @mcp.tool()
        async def qa_bug_report(description: str, ctx: Context) -> str:
            """Start a structured bug report from a plain-language description. Chat-only: the server makes NO model call. It returns a task envelope that YOU answer, then call qa_submit_bug_report with the task_id and your markdown report."""
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
            """Start the next step of an exploratory-testing coaching session. Chat-only: the server returns a task envelope that YOU answer, then call qa_submit_explore_step with the task_id and your coaching step. Pass a stable session_id to keep coverage memory across calls; include tester_response with what you observed after the previous step."""
            return await _tracked(
                "qa_explore_step",
                ctx,
                mcp_handlers.handle_explore_step(
                    feature, session_id, tester_response, progress=_make_progress(ctx)
                ),
            )

        @mcp.tool()
        async def qa_submit_bug_report(task_id: str, report: str, ctx: Context) -> str:
            """Submit the bug report YOU wrote for a task opened by qa_bug_report: pass the task_id and the full markdown as `report`, written exactly as the envelope's system_prompt specifies. The server validates the required sections, saves it to the corpus, and either returns the finished report or asks you to re-emit it against a NEW task id."""
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
                """Push a stored test suite into TestRail or Xray. target="testrail" (needs the numeric project_id from the TestRail URL) or "xray". Defaults to a PREVIEW that sends nothing. A real push needs apply=true AND the target's kill-switch flag enabled in .env. Nothing here can delete the cases afterwards."""
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
                """Create a new API test project, or continue an existing one. Every API flow starts here. With NO arguments it returns the choice plus registered projects: ask the tester in plain chat. create="<name>" fetches the public template, renames it, makes ONE local commit (nothing pushed) and proves it compiles. use="<name or path>" continues an existing project. Then call qa_prepare_api_tests."""
                return await _tracked_noted(
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
                """Start (or continue) an API endpoint test intake. Chat-only: no model call. Paste a contract template, curl command, OpenAPI URL/JSON or prose. Returns an intake card (questions to ask) or, once complete and confirmed=true, a task envelope YOU answer, then call qa_submit_api_tests with the task_id and your cases. project="<name>" scopes the endpoint registry; pass it once, on the first call."""
                return await _tracked_noted(
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
                """Submit the API test cases YOU generated for a qa_prepare_api_tests task: the task_id and your JSON {"cases": [...]}. The server grounds every assertion against the confirmed contract (dropping hallucinated fields, refusing cases that cannot fail) and returns the suite + a suite_id for qa_write_api_test."""
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
                """Render + (dry-run or) write the Java tests for a finalized suite. apply=false (default) returns the branch, target paths and Java source, writing nothing. apply=true writes via the framework repo's ops pipeline, only when QA_API_FRAMEWORK_WRITE_ENABLED is on and QA_API_FRAMEWORK_WRITE_DRY_RUN is off. Never main, never push. project="<name>" targets a qa_api_project project; omit it for QA_API_FRAMEWORK_PATH."""
                return await _tracked_noted(
                    "qa_write_api_test",
                    ctx,
                    mcp_handlers.handle_write_api_test(
                        suite_id, apply, project, progress=_make_progress(ctx)
                    ),
                )

        @mcp.tool()
        async def qa_submit_explore_step(task_id: str, step: str, ctx: Context) -> str:
            """Submit the coaching step YOU wrote for a task opened by qa_explore_step. Include the trailing <meta>area: …; phase: …</meta> line the system_prompt asks for: the server parses it to track coverage, then strips it."""
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
        """DEPRECATED: Jira needs no credentials here; tickets are read through YOUR OWN Atlassian MCP connection. This tool returns connection steps and stores NOTHING; never ask for an API token. If a ticket URL fails, call qa_prepare_test_cases and follow its directive. To VERIFY the connection, call with no arguments (it directs you to mcp__atlassian__atlassianUserInfo), then again with atlassian_verify_json set to that RAW JSON result (or {"error": "..."})."""
        return await _tracked_noted(
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

    @mcp.tool()
    async def qa_mirror_hold(ctx: Context, serial: str, action: str = "status") -> str:
        """Desktop Mirror only: acquire|release|status ONE device's lock so no run drives a mirrored phone. No device command; returns JSON (owner, mirror_hold)."""

        async def _hold() -> str:
            return mcp_handlers.handle_mirror_hold(serial, action)

        return await _tracked("qa_mirror_hold", ctx, _hold())

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
        serial: str = "",
        peek: bool = False,
    ) -> list[ContentBlock]:
        """Capture screenshots from a connected phone/emulator/simulator: images PLUS one capture_id per screen. Use it to ground test cases in REAL screens, especially for a Jira ticket (its images are unreadable here): pass the `capture_ids` to `qa_prepare_test_cases`/`qa_generate_test_cases`. `count` = screens. `peek`=true: text. `names` (comma-separated) ONLY if the user told you; never ask. `serial` aliases `device_id`; omit it for a picker; ids expire after 30 minutes."""
        from mcp.types import TextContent

        text, specs = await _tracked(
            "qa_capture_screens",
            ctx,
            mcp_handlers.handle_capture_screens(
                device_id=_device_ref(device_id, serial),
                count=count,
                rescan=rescan,
                names=names,
                peek=peek,
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
            """Guided entry point: pick a workflow (Test cases / Bug report / Exploratory); it walks you END-TO-END, asking where the feature comes from (description / Jira ticket / mobile screens / Jira + mobile), and returns the suite. Feature Analysis: `qa_feature_analysis`. No parameters; without MCP elicitation, a markdown menu."""
            return await _tracked(
                "qa_wizard",
                ctx,
                mcp_handlers.handle_wizard(
                    **_make_elicitors(ctx),
                    progress=_make_progress(ctx),
                ),
            )

    @mcp.tool(name="qa-doctor")
    async def qa_doctor(ctx: Context, fix: bool = False) -> str:
        """Is THIS machine ready? Verdict, environment, integrations (Jira/Atlassian), CLI tooling (adb/xcrun), features, action items. Reports NO model backend: every generative step runs in YOUR chat model. Read-only by default; fix=true repairs what it can (rewrites `.env` with a backup, writes the hosted `atlassian` entry into this client's MCP config). Run first on a new machine."""
        progress = _make_progress(ctx)
        # Resolved BEFORE entering _tracked: this is a round trip back to the
        # client, not part of the report's own work, and _tracked owns the
        # in-flight counter that gates the drift restart.
        roots = await _workspace_roots(ctx)
        return await _tracked_noted(
            "qa-doctor",
            ctx,
            mcp_handlers.handle_setup_check(
                progress=progress, workspace_roots=roots, fix=fix
            ),
        )

    # ALL editions, NO flag: flag policy says a new feature ships ON, and this
    # one falls into none of the four flag categories -- it makes no outbound
    # call, needs no per-install config, is not an experiment, and there is no
    # install where "do not tell the tester what this machine allows" is the
    # right value. Registered UNCONDITIONALLY here and named in the ambient
    # instructions block, which a test cross-checks per edition. Its producer is
    # tools/host_privileges.py, top level, so this survives the test-cases-only
    # edition, which ships no tools/mobile/ on disk.
    @mcp.tool()
    async def qa_host_check(ctx: Context, refresh: bool = False) -> str:
        """What OS is this and can this account elevate? Call BEFORE proposing any install or provision command. ADVISORY ONLY: blocks nothing, names the admin-free route first, reports "cannot determine" as UNDETERMINED, never prompts for a password. Cached per process; `refresh=true` re-probes."""
        return await _tracked_noted(
            "qa_host_check",
            ctx,
            mcp_handlers.handle_host_check(refresh=bool(refresh)),
        )

    # ALL editions, NO flag. It makes no outbound call, needs no per-install
    # credential, is not an experiment, and there is no install where "do not
    # tell the client what this machine is" is the right value -- so it falls
    # into none of the four flag categories and owes no FEATURE_FLAGS entry.
    # Deliberately NOT named in tools/guidance.py: it is for a GUI client, not
    # for a chat model, and naming it there would spend instruction budget on a
    # tool no chat should call.
    @mcp.tool()
    async def qa_machine_report(ctx: Context, section: str = "all") -> str:
        """Machine-readable rows about THIS install for a non-chat client. JSON: `backend`, `doctor` (component/status/detail/fix_hint rows), `clients` (MCP clients and whether their entry points at THIS install) and `provisioning`. READ-ONLY, safe to poll; `qa-doctor` is the human-read one."""
        return await _tracked_noted(
            "qa_machine_report",
            ctx,
            mcp_handlers.handle_machine_report(section),
        )

    @mcp.tool(name=selfcheck_module.SELF_TOOL_NAME)
    async def qa_selfcheck(ctx: Context) -> str:
        """Check whether THIS build's own replies still describe it: call every registered tool with defaults; report any reply naming a deleted setting or module, a model this server cannot call, or a tool this edition does not register. Read-only: no `apply=true`, outbound connections blocked. `qa-doctor` covers the MACHINE."""
        return await _tracked_noted(
            selfcheck_module.SELF_TOOL_NAME,
            ctx,
            mcp_handlers.handle_selfcheck(server=mcp),
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
            serial: str = "",
        ) -> list[ContentBlock]:
            """Start a compact enterprise Feature Analysis Report. Chat-only: no model call; it returns a task envelope YOU answer, then call qa_submit_feature_analysis with the task_id and your JSON report. mode: jira (description or Jira URL), mobile (capture device screens) or jira_mobile; omit it and the server asks. A Jira URL may first return a DIRECTIVE: fetch with your own mcp__atlassian__getJiraIssue and call again with jira_content_json set to the raw result as a JSON STRING."""
            from mcp.types import TextContent

            reply = await _tracked_noted(
                "qa_feature_analysis",
                ctx,
                mcp_handlers.handle_feature_analysis(
                    feature_or_url,
                    mode=mode,
                    device_id=_device_ref(device_id, serial),
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
            """Submit the Feature Analysis JSON YOU wrote for a task opened by qa_feature_analysis. Produce a SINGLE JSON object matching the envelope's response_schema and pass it with the task_id as `report_json`. The server validates it, renders the report, and, if the submission carried no usable object, gives ONE resubmit round against a new task_id."""
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
        mobile=edition_mobile,
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
    _register_mobile_defaults()
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

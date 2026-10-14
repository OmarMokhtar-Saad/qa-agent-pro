"""Server construction: edition gates, instructions suffix and tool registration.

``fastmcp`` and ``mcp.types`` are optional: they are ``None`` here when the
extra is missing, and only ``build_server`` (starting the server) requires them. The facade
``mcp_server`` owns the ``rt`` handed to every ``register_*`` call, so tests
that patch a facade name still bite.
"""

from __future__ import annotations

from typing import Any, NamedTuple, cast

from config.settings_guard import never_raise
from handlers import (
    _common,
    _lane,
    core_tools,
    corpus_and_device_tools,
    feature_analysis_tools,
    full_edition_submit_tools,
    full_edition_tools,
    machine_tools,
    mcp_srv_settings,
    mcp_srv_state,
    prompts,
    setup_check,
    suite_tools,
)
from handlers.mcp_srv_state import logger
from tools import guidance, mcp_rejection_log


def _register_mobile_defaults() -> None:
    """Register the mobile lane's default dialog handlers, install sources and
    tree sources once at startup. Every register call is idempotent; a failure
    is logged and leaves the rest of the server up."""
    if not _common.mobile_lane_enabled():
        return
    try:
        _lane.mobile_lane.register_defaults()
    except (ImportError, OSError, RuntimeError, ValueError):
        logger.warning("mobile default registration failed", exc_info=True)


# The `instructions=` suffix naming a pending drift-restart marker, or "".
#
# P1-4 (Air onboarding fix): a drift restart silently swaps the process
# under a tester mid-session; the reload marker was previously read only
# by qa-doctor's next call. Peeking at it here too -- in the
# `instructions=` block every client reads at `initialize`, before any
# tool call -- reaches the tester's actual next action. PEEK, never
# consume: the marker is one-shot and qa-doctor's reload-outcome report
# must still find it. Read-only report, never a `_tracked()` side effect:
# never raises, never blocks startup.
def _reload_notice() -> str:
    """The `instructions=` suffix naming a pending drift-restart marker, or ""."""
    with never_raise():
        marker = setup_check.peek_reload_marker() or {}
        if marker.get("reason"):
            return (
                "\n\nNOTE: this server restarted mid-session ("
                f"{marker.get('reason')}) to pick up a change. If a reply "
                "looks stale, say so and I will retry."
            )
    return ""


# Log the calls FastMCP rejects on argument validation (they never reach
# `_tracked`). Guarded twice: the test doubles lack `add_middleware`, and the
# pinned fastmcp (3.4.7) is unverified against the installed 2.14.7.
def _install_rejection_log(mcp) -> None:
    """Install the argument-validation rejection log middleware, if supported."""
    with never_raise(logger, "rejection-log middleware not installed", exc_info=True):
        if hasattr(mcp, "add_middleware"):
            _rejection_log = mcp_rejection_log.build_middleware()
            if _rejection_log is not None:
                mcp.add_middleware(_rejection_log)


class _Kit(NamedTuple):
    """The lazily imported fastmcp types every registrar receives."""

    context: Any
    content_block: Any
    annotations: Any


def _register_first(mcp, kit: _Kit, rt) -> None:
    core_tools.register_core_tools(mcp, kit, rt)
    suite_tools.register_suite_tools(mcp, kit, rt)
    if _common.mobile_lane_enabled():
        _lane.mobile_lane.register(mcp, kit, rt)
    full_edition_tools.register_full_edition_tools(mcp, kit, rt)
    full_edition_submit_tools.register_full_edition_submit_tools(mcp, kit, rt)


try:
    from fastmcp import Context, FastMCP
    from mcp.types import ContentBlock, ToolAnnotations
except ImportError as exc:
    if (exc.name or "").split(".")[0] not in ("fastmcp", "mcp"):
        raise
    Context = FastMCP = ContentBlock = ToolAnnotations = cast("Any", None)


def _register_rest(mcp, kit: _Kit, rt) -> None:
    corpus_and_device_tools.register_corpus_and_device_tools(mcp, kit, rt)
    machine_tools.register_machine_tools(mcp, kit.context, rt)
    feature_analysis_tools.register_feature_analysis_tools(mcp, kit, rt)


# ``fastmcp`` is an optional module-level import (``None`` when the extra is
# missing), so importing this module never requires it — only actually
# starting the server does. ``rt`` is the facade module: handlers read its names.
def build_server(rt):
    """Construct and return the FastMCP server with every qa_* tool registered."""
    if FastMCP is None:
        raise ImportError(
            'fastmcp is not installed: pip install -e ".[mcp]"', name="fastmcp"
        )
    mcp, editions = _new_fastmcp(FastMCP)
    _install_rejection_log(mcp)
    kit = _Kit(Context, ContentBlock, ToolAnnotations)
    _register_first(mcp, kit, rt)
    _register_rest(mcp, kit, rt)
    prompts.register_prompts(mcp, prompts.PromptEditions(**editions), rt)

    return mcp


# The two edition gates, read here so the GUIDANCE text is built from the
# same expressions the registration gates below use. (Those gates still
# call `test_cases_only()` inline at their own sites; this is a second
# read of one pure function, not a second source of truth.) The guidance
# names tools, and a client that calls a tool this edition never registered
# fails mid-workflow in front of a tester -- so the text and the
# registration must not be able to disagree.
#
# The mobile lane's gate is a single named predicate rather than an
# expression, and it is read here for the same reason as the two above: the
# guidance names three tools and must not be able to name them on an
# edition that does not register them. Its own definition is the
# whole gate (the modules on disk), so nothing at the
# registration site below may add a second term -- a test asserts that site
# holds nothing but a call to this.
def _edition_gates() -> dict:
    return {
        "test_cases_only": _common.test_cases_only(),
        "api_tests": mcp_srv_settings.api_tests_enabled(),
        "mobile": _common.mobile_lane_enabled(),
    }


def _new_fastmcp(fastmcp_cls):
    """Build the bare FastMCP server; return it with the three edition gates.

    The gates come back as the fields of ``prompts.PromptEditions``.
    """
    editions = _edition_gates()
    mcp = fastmcp_cls(
        mcp_srv_state.SERVER_NAME,
        instructions=guidance.server_instructions(**editions) + _reload_notice(),
    )
    return mcp, editions

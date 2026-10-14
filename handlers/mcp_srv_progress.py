"""Progress pings: adapt a FastMCP Context into the handlers' progress callback."""

from __future__ import annotations

import asyncio

from config.settings_guard import never_raise
from handlers import _lane
from handlers.mcp_srv_client import _note_client
from handlers.mcp_srv_state import logger

# A progress ping is a NOTIFICATION: the tool does not need its result and
# must never wait on it. Bounded well below the client-side tool timeout the
# ping exists to reset, so a stalled client costs one dropped ping rather
# than the call. Same reasoning and same order of magnitude as
# ROOTS_TIMEOUT_S; kept separate because they answer to different clients'
# capabilities and should be tunable apart.
_PROGRESS_TIMEOUT_S = 5.0


def progress_timeout_s() -> float:
    return _PROGRESS_TIMEOUT_S


def _ping_sender(ctx, state):
    """The ``progress(message)`` coroutine function bound to one Context."""

    async def progress(message: str) -> None:
        state["n"] += 1
        with never_raise(
            logger, "mcp report_progress failed for %r", message, exc_info=True
        ):
            await asyncio.wait_for(
                ctx.report_progress(progress=state["n"], total=None, message=message),
                timeout=progress_timeout_s(),
            )

    return progress


def _bind_watch(progress) -> None:
    """Bind the callback for long mobile waits; the mobile modules may be absent."""
    # S16: a long mobile wait (boot, a loading screen) reports through the same
    # channel. Bound per call context; the mobile modules may be absent.
    if _lane.watch is not None:
        _lane.watch.bind_progress(progress)


# Every call reports incremental progress so a long-running generation or
# device run resets the MCP client's tool-call timeout (progress notifications
# keep the stream alive). Best-effort — a transport hiccup never breaks the
# tool.
#
# 2026-09-01: "never breaks the tool" was true only for a client that
# RAISES. A client that accepts the notification and never completes the
# await is not an exception, and the ``except Exception`` below cannot see
# it -- the tool call simply hung, forever, on a best-effort progress ping.
# Every other client round trip in this file is already bounded
# (:func:`_workspace_roots` by ``ROOTS_TIMEOUT_S``, both elicitation tiers
# by ``asyncio.wait_for``); this was the one that was not, and it is the
# most frequent of them by a wide margin. The timeout feeds the SAME
# ``except`` clause -- ``asyncio.TimeoutError`` is an ``Exception`` on every
# version this project supports -- so a slow client degrades to exactly the
# behaviour a raising one always had: the ping is dropped, logged at DEBUG,
# and the tool continues.
def _make_progress(ctx):
    """Adapt a FastMCP Context into the handlers' ``(message)->awaitable`` callback."""
    _note_client(ctx)  # every tool builds one of these — cheap host detection
    state = {"n": 0}
    progress = _ping_sender(ctx, state)
    _bind_watch(progress)
    return progress

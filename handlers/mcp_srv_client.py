"""MCP client identity and the client's open workspace roots."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import unquote, urlparse

import llm
from config.settings_guard import never_raise
from handlers import _elicit
from handlers import mcp_srv_state as state
from handlers.mcp_srv_state import logger
from tools.log_setup import set_client


# Forward the MCP client's name (initialize clientInfo) to the LLM layer
# so QA_LLM_BACKEND=auto can match the backend to the host editor, and
# record it (name + version) for telemetry tagging.
def _note_client(ctx) -> None:
    """Forward the MCP client's name to the LLM layer and record it."""
    with never_raise(logger, "could not read clientInfo", exc_info=True):
        info = ctx.session.client_params.clientInfo
        name = info.name
        llm.set_host_client(name)
        state.client_info()["name"] = (name or "").strip().lower()
        state.client_info()["version"] = str(getattr(info, "version", "") or "")
        _forward_client(name)
        _tag_log_lines(name)


def _forward_client(name) -> None:
    # ...and to the per-client elicitation record, which keys its strikes by
    # this name so one editor's verdict can never gate another's on a shared
    # install. Never raises (see handlers._elicit.note_elicit_client).
    _elicit.note_elicit_client(name)
    # v1.97.0 cursor-hardening (item 6a): forward clientInfo a second
    # time so handlers._elicit._dispatch_provenance can stamp it onto
    # every saved suite -- both already-set values at this point
    # (_CLIENT["name"]/["version"] just above).
    _elicit.note_provenance_client(name, state.client_info().get("version", ""))


def _tag_log_lines(name) -> None:
    # Tag this process's log lines with the editor that owns it: on an
    # install shared by three clients, the pid alone does not say WHICH.

    set_client(name, Path(__file__).resolve().parents[1] / "data" / "logs")


# The tester's OPEN workspace, per the MCP `roots` capability -- the only
# authoritative answer to "where is the project-scoped mcp.json?". Bounded on
# both axes: a client may legitimately report several folders, and the request is
# a round trip to a client that could accept it and never reply.
_MAX_WORKSPACE_ROOTS = 8
ROOTS_TIMEOUT_S = 5.0


# The client's open workspace folder(s) as local filesystem paths.
#
# Needed because the project-scoped `.cursor/mcp.json` / `.mcp.json` a tester
# would actually edit lives in their editor workspace, which this stdio
# subprocess cannot otherwise locate: a dist install sits in a fixed
# directory, and `Path.cwd()` is useless here because this module chdir()s to
# its own install root at import time (see the top of the file). Two guesses
# shipped on that reasoning and BOTH were wrong in production (v1.31.0,
# v1.32.0); `roots` is the protocol's own answer.
#
# Best-effort and NEVER raises: `roots` is an OPTIONAL client capability, so an
# unsupported client can surface anything from a protocol error to an
# AttributeError to silence. Any failure returns [] and every caller degrades
# to its previous behaviour.
async def _workspace_roots(ctx) -> list[Path]:
    """The client's open workspace folder(s) as local filesystem paths."""
    with never_raise(logger, "mcp list_roots unavailable", exc_info=True) as g:
        roots = await asyncio.wait_for(ctx.list_roots(), timeout=ROOTS_TIMEOUT_S)
    if g.failed:
        return []
    try:
        reported = list(roots or [])[:_MAX_WORKSPACE_ROOTS]
    except TypeError:
        logger.debug("mcp list_roots returned an unusable result", exc_info=True)
        return []
    return _local_paths(reported)


def _local_paths(reported) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for root in reported:
        path = _root_path(root)
        if path is None:
            continue
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _root_path(root) -> Path | None:
    try:
        parsed = urlparse(str(getattr(root, "uri", "") or ""))
        # The MCP spec allows file:// only, and a host component other than
        # localhost is a remote/UNC location this process must not read.
        if parsed.scheme != "file":
            return None
        if (parsed.netloc or "").lower() not in ("", "localhost"):
            return None
        raw = unquote(parsed.path)  # %20 and friends
        if not raw:
            return None
        return Path(raw)
    except (ValueError, TypeError, AttributeError):
        logger.debug("skipping unusable MCP root %r", root, exc_info=True)
        return None

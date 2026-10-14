"""FastMCP server exposing the QA agents/tools to Claude Desktop, Claude Code,
and Cursor over stdio (gated by QA_MCP_ENABLED, default OFF).

Run:      python mcp_server.py        (or: python -m mcp_server)
Requires: pip install -e ".[mcp]"      (installs the optional ``fastmcp`` extra)

Each registered ``qa_*`` tool is a thin adapter that turns the FastMCP request
``Context`` into a plain async progress callback and delegates to
``handlers/`` — which holds all business logic, audit logging, and
concise-markdown shaping. No LLM call, prompt assembly, or secret handling lives
here; ``fastmcp`` is imported lazily inside ``build_server`` so this module (and
the mocked test suite) can load without the optional dependency installed.

NOTE: this module must NOT use ``from __future__ import annotations`` — string
annotations would make pydantic evaluate the ``ctx: Context`` hints against the
module globals, where the lazily-imported ``Context`` is not defined (NameError
at real FastMCP tool registration). Eager annotations resolve ``Context`` at
decoration time inside ``build_server``, where it is in scope.
"""

import os
import sys
import time  # noqa: F401  (deliberate setattr target: tests patch srv.time)
from pathlib import Path

# Anchor the working directory to the repo root BEFORE importing settings:
# config.settings loads the project .env from the cwd, and the data paths
# (suite store, corpus, audit log) are cwd-relative. MCP clients may spawn
# this server from an arbitrary directory, so re-anchor defensively.
os.chdir(Path(__file__).resolve().parent)

import tools.selfcheck as selfcheck_module  # noqa: E402, F401, I001  (public name)
from handlers import mcp_srv_build, mcp_srv_main  # noqa: E402
from handlers.mcp_srv_settings import settings  # noqa: E402, F401
from handlers.mcp_srv_build import (  # noqa: E402, F401
    _new_fastmcp,
    _register_mobile_defaults,
    _reload_notice,
)
from handlers.mcp_srv_client import (  # noqa: E402, F401
    _MAX_WORKSPACE_ROOTS,
    _note_client,
    _workspace_roots,
)
from handlers.mcp_srv_content import (  # noqa: E402, F401
    MobileReplySpec,
    _image_content_blocks,
    _mobile_reply,
    _prepare_payload_to_content,
    _with_script_doc,
)
from handlers.mcp_srv_driftloop import _drift_watch  # noqa: E402, F401
from handlers.mcp_srv_elicit import (  # noqa: E402, F401
    _make_asker,
    _make_chooser,
    _make_elicitors,
    _prepare_call,
)
from handlers.mcp_srv_logsetup import _configure_logging  # noqa: E402, F401
from handlers.mcp_srv_progress import _make_progress  # noqa: E402, F401
from handlers.mcp_srv_recent import (  # noqa: E402, F401
    _DUP_REPLY_PREFIX,
    _generate_cache_key,
    _recent_call_cached,
    _recent_call_store,
)
from handlers.mcp_srv_state import (  # noqa: E402, F401
    _DRAIN_IDLE_S,
    _GENERATE_DUP_WINDOW_S,
    _INFLIGHT,
    _RECENT_CALL_TTL_S,
    logger,
)
from handlers.mcp_srv_toolcall import _device_ref  # noqa: E402, F401
from handlers.mcp_srv_track import _tracked, _tracked_noted  # noqa: E402, F401


def build_server():
    """Construct the FastMCP server; handlers read the facade as ``rt``."""
    return mcp_srv_build.build_server(sys.modules[__name__])


def main() -> None:
    """Entry point. Gated behind QA_MCP_ENABLED (default OFF) — with the flag off
    the server refuses to start rather than silently exposing the tools."""
    _configure_logging()
    if not mcp_srv_main.gate_open():
        return
    mcp_srv_main.disclose_degraded_llm()
    mcp_srv_main.announce_startup()
    server = build_server()
    _register_mobile_defaults()
    mcp_srv_main.announce_server_start()
    mcp_srv_main.start_drift_thread(_drift_watch)
    logger.info("Starting the qa-agents MCP server over stdio…")
    server.run(show_banner=False)


if __name__ == "__main__":
    main()

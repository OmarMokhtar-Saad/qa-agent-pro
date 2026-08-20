"""What is left of the LLM access layer.

**This module makes no LLM call and has no backend.** On 2026-08-16 (dead-code
deletion P2-G2c) the four public coroutines -- ``ask``, ``ask_json``,
``ask_vision`` and ``warm_cache_prefix`` -- were deleted together with all three
backends (``cli``, ``api``, ``cursor``), the shared JSON parsing and repair
ladder, the structured-JSON / forced-tool-use machinery, the prompt-cache
prefix, the usage recording and every availability probe.

Why, in one line: nothing called them. Test-case generation has been chat-only
and unconditional since 2026-08-12, and the deletion programme's P2-F and P2-G
batches removed the remaining call sites one at a time -- the Jira image
descriptions and the prompt-cache warm-up (P2-F1/F2), the AC synthesis and the
checklist decomposition (P2-F2), risk scoring and the two test-plan builders
(P2-F3), and finally ``tools/rtm``'s entailment and adjudication tiers (P2-G1),
which held the last ``ask_json`` calls in the tree. The three backends then had
no caller at all, and `qa-doctor`'s backend blocker was telling testers to fix
something that could not affect any flow (P2-G2b).

Reviving a server-side LLM call is a NEW implementation and a deliberate
architectural decision, not a flag flip -- ``git show <this commit>~1`` is the
last tree that carried one. The house rule it would have to satisfy is in
CLAUDE.md: fold the step into an existing prepare/submit boomerang as a
``HostJob``, or open a task through the generic broker ``tools/host_llm.py``.

What survives here is the small, model-free surface other modules still read:
the MCP client's name (so tester-facing text can name the editor in use), the
generation-mode resolver (a ``"host"`` constant with a live ``"server"``/``"host"``
contract), and the error-string sanitiser the chat-only bug reporter and
exploratory coach use on host-supplied text.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# The connected MCP client
# --------------------------------------------------------------------------- #
# The client announces itself in the initialize handshake (clientInfo.name) and
# mcp_server.py forwards it here. It used to drive QA_LLM_BACKEND=auto; with no
# backend left, its remaining job is tester-facing text -- tools/jira_mcp.py
# tailors the Atlassian-MCP connect instructions to the editor actually in use,
# because "add this to your Cursor config" is useless in Claude Desktop.

_HOST_CLIENT = {"name": ""}


def set_host_client(name: str) -> None:
    """Record the MCP client's name from the initialize handshake."""
    _HOST_CLIENT["name"] = (name or "").strip().lower()


def get_host_client() -> str:
    """Return the connected MCP client's name (lowercased), or '' if unknown.

    Read-only counterpart to set_host_client. Never raises."""
    return _HOST_CLIENT["name"]


def resolve_generation_mode() -> str:
    """Resolve the effective test-generation mode ('server' or 'host').

    ALWAYS returns "host". Test-case generation has been chat-only since
    2026-08-01, and on 2026-08-12 the residual QA_GENERATION_MODE setting and
    its _coerce_generation_mode validator were deleted (flag-surface reduction,
    batch 1). The 8-category fan-out always runs on the tester's OWN chat model.

    Since 2026-08-16 there is not even a backend to route to: dead-code deletion
    P2-G2c removed all three. The function is RETAINED, with its
    'server'|'host' contract unchanged, because callers exist
    (tools/mcp_handlers.py) and the 'server' value stays meaningful to them --
    they branch on it to decide what to disclose. It simply never returns it.
    Never raises.
    """
    return "host"


def sanitize_llm_response(raw: str, friendly_msg: str) -> str:
    """Return raw unchanged unless it starts with 'Error:' — then return friendly_msg.

    Prevents raw error strings from leaking to end users. Still live on the
    chat-only paths: agents/bug_report_agent.py and
    agents/exploratory_coach_agent.py run it over text the HOST supplied, which
    can carry an error string from the host's own model.
    """
    if raw.startswith("Error:"):
        logger.warning("LLM error response sanitized: %s", raw[:200])
        return friendly_msg
    return raw

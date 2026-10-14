"""Jira MCP: verify directive helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from tools.jira_mcp_connect import connect_steps
from tools.jira_mcp_tool_names import access_tool_name, verify_tool_name

logger = logging.getLogger("tools.jira_mcp")


def _verify_calls(tool: str, access: str) -> list[str]:
    """The heading and the two read-only probe calls."""
    return [
        "\U0001f9ea **Want a verified answer instead of a guess?** "
        "Agent: do this now, yourself, without asking the user anything.",
        "",
        f"1. Call `{tool}` (no parameters). It is read-only and only "
        "reports who your Atlassian connection is authenticated as.",
        f"2. Call `{access}` (no parameters) too. Also read-only: it "
        "lists the Atlassian sites that sign-in can actually reach, "
        "which is what catches a session on the wrong account. Skip "
        "this one if it is not in your tool list.",
    ]


def _verify_report(tool: str) -> list[str]:
    """The hand-back step, the failure fallback and the privacy note."""
    return [
        "3. Call `qa_configure_jira` again with `atlassian_verify_json` "
        'set to `{"user": <result 1>, "resources": <result 2>}`, both '
        "RAW (do not summarise, reword or truncate them). Send just "
        "result 1 if you could not make the second call.",
        f"4. If `{tool}` is not in your tool list, or the call fails, "
        "call `qa_configure_jira` with "
        '`atlassian_verify_json={"error": "<what happened>"}` instead '
        "-- I'll turn that into the exact connection steps for this "
        "editor.",
        "",
        "Your identity is not stored, and neither is any site address: "
        "the results are read once, reported back in the same turn, and "
        "discarded. Only the yes/no, the time, and the names of the "
        "tools called are recorded, so this check is not repeated on "
        "every report.",
    ]


# Directive asking the calling agent to PROVE the Atlassian connection.
#
# Appended to qa_configure_jira's no-argument reply only -- NOT to
# qa-doctor (2026-09-25): the probe call opens the OAuth sign-in, which must
# happen only when the tester asks for Jira data or runs qa_configure_jira.
# Additive by design: the on-disk best guess (connect_hint_line) still stands
# beside it, because this only becomes a real status once the agent calls
# back.
def verify_directive() -> str:
    """Directive asking the calling agent to PROVE the Atlassian connection.
    Never raises."""
    try:
        tool = verify_tool_name()
        access = access_tool_name()
        return "\n".join(_verify_calls(tool, access) + _verify_report(tool))
    except Exception:
        logger.exception("verify_directive failed - falling back to connect steps")
        return connect_steps()

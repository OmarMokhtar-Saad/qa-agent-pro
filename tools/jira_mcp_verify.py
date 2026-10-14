"""Jira MCP: verify helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from tools.jira_mcp_connect import connect_steps
from tools.jira_mcp_identity import _unwrap_mcp_content as unwrap_mcp_content
from tools.jira_mcp_verify_parse import _parse_verify_payload
from tools.jira_mcp_verify_probe import _probe_and_sites, _probe_verdict

logger = logging.getLogger("tools.jira_mcp")


def verify_result_message(raw: object) -> str:
    """The tester-facing message half of :func:`verify_outcome`.

    Kept as the public name every existing caller and test uses; the verdict
    itself is now available separately so it can be PERSISTED without parsing
    the untrusted payload a second time.
    """
    return verify_outcome(raw)[1]


# Turn the agent's raw ``atlassianUserInfo`` result into a verdict.
#
#     Returns ``(status, message)`` where status is one of:
#
#     * ``"verified"``           -- identity proven AND the session can reach the
#       configured Jira site;
#     * ``"verified_no_access"`` -- identity proven, access NOT checked: no site
#       list supplied, no ``JIRA_BASE_URL`` to compare against, or the list came
#       back unusable. This is the pre-2026-09-01 guarantee, stated honestly;
#     * ``"wrong_site"``         -- identity proven, and the sites that session
#       can reach do not include the configured Jira host: ticket URLs will fail;
#     * ``"failed"``             -- an error marker came back (really not
#       connected);
#     * ``"unknown"``            -- nothing supplied, unreadable, oversized, or
#       empty.
#
#     Only ``"verified"`` and ``"failed"`` are worth persisting. ``"unknown"``
#     means nothing was established, so it must NOT overwrite an earlier verdict:
#     an agent that fumbles the hand-back must not be able to erase a good result.
#
#     Five outcomes, none of which raises and none of which is a dead end:
#
#     * nothing supplied -> the directive again (nothing was actually checked);
#     * an identity payload -> VERIFIED, with a best-effort identity;
#     * an error marker -> NOT CONNECTED, plus this client's connect steps;
#     * an empty object -> an honest "couldn't confirm", plus the hint line;
#     * anything unreadable -> an honest "couldn't read that", plus the hint line.
#
#     The payload is UNTRUSTED: size-capped BEFORE parsing, ``json.loads`` only
#     (never eval), no assumed schema, and every echoed fragment sanitized.
#     Nothing from it is stored or logged. Never raises.
def verify_outcome(raw: object) -> tuple[str, str]:
    """Turn the agent's raw verification result into ``(status, message)``.
    Never raises."""
    try:
        parsed = _parse_verify_payload(raw)
        if isinstance(parsed, tuple):
            return parsed
        probe, sites = _probe_and_sites(unwrap_mcp_content(parsed))
        return _probe_verdict(probe, sites)
    except Exception:
        logger.exception("verify_outcome failed - falling back")
        return (
            "unknown",
            "\u26a0\ufe0f I couldn't read that verification result.\n\n"
            + connect_steps(),
        )

"""The qa_configure_jira handler: connection check reply and the deprecated config shim."""

from __future__ import annotations

import logging
from typing import NamedTuple

from handlers import _common
from handlers.jira_text import _echoed_jira_host
from handlers.jira_verify import _verified_jira_reply
from tools.jira_mcp import connect_steps, verify_directive

logger = logging.getLogger("tools.mcp_handlers")


class ConfigureJiraArgs(NamedTuple):
    """The four ``qa_configure_jira`` arguments (all optional, all ignored but one)."""

    base_url: str = ""
    email: str = ""
    api_token: str = ""
    atlassian_verify_json: str = ""


# 2026-08-01: there are no Jira credentials for this server to configure.
#
# Jira is read through the CALLING AGENT's own Atlassian MCP connection
# (mcp.atlassian.com, OAuth 2.1, Jira Cloud), so nothing is stored here any
# more. The tool is KEPT rather than removed: clients cache their tool list,
# and testers still say "configure Jira" -- so it must answer with the real
# next step instead of vanishing.
#
# Deliberately writes NOTHING. Silently accepting an API token the server can
# no longer use would be the worst outcome: the tester would believe Jira was
# wired up and then get an empty ticket. The arguments are accepted, ignored,
# and never logged or echoed. Never raises.
#
# 2026-08-03 -- this is ALSO the return half of the live connection check.
# Called with no arguments it now hands the agent a directive to make one
# read-only ``atlassianUserInfo`` call; called back with that call's raw JSON
# in ``atlassian_verify_json`` it parses the blob defensively (size-capped,
# ``json.loads`` only, never eval, no assumed schema) and reports a REAL
# verified / not-connected verdict. That blob is read once, never persisted
# and never logged -- only its PRESENCE reaches the audit record, and only the
# resulting yes/no, its timestamp and the probe tool's NAME reach the verdict
# store (``tools/verify_store.py``). No account id, email or display name is
# kept, which is what the tester-facing reply promises.
async def handle_configure_jira(
    args: ConfigureJiraArgs | None = None, *, progress: _common.ProgressCb = None
) -> str:
    """Report how Jira is connected; stores nothing, verifies on a hand-back."""
    args = args or ConfigureJiraArgs()
    try:
        host, malformed_url = _echoed_jira_host(args.base_url)
        await _common.emit(progress, "\U0001f517 Checking how Jira is connected…")
        verification = str(args.atlassian_verify_json or "").strip()
        await _audit_configure_jira(bool(verification))
        if verification:
            return await _verified_jira_reply(verification)
        had_credentials = bool(args.email or args.api_token)
        return _jira_connect_reply(host, malformed_url, had_credentials)
    except Exception as exc:
        logger.exception("handle_configure_jira failed")
        _common.capture_error(exc, "qa_configure_jira")
        return connect_steps()


async def _audit_configure_jira(verification_supplied: bool) -> None:
    """Audit row for `handle_configure_jira`; nothing is stored, only PRESENCE."""
    await _common.audit(
        "mcp_configure_jira",
        detail={
            "mode": "mcp",
            "credentials_stored": False,
            # PRESENCE only -- the payload itself is never logged.
            "verification_supplied": verification_supplied,
        },
    )


def _connect_preamble(malformed_url: bool, had_credentials: bool) -> str:
    """The notices that lead the "nothing was saved" reply."""
    preamble = (
        "ℹ️ **Nothing was saved -- and nothing needs to be.**\n\n"
        if had_credentials
        else ""
    )
    if malformed_url:
        preamble += (
            "⚠️ That URL looked malformed (it contained a line "
            "break or control character), so I ignored it.\n\n"
        )
    return preamble


def _jira_connect_reply(host: str, malformed_url: bool, had_credentials: bool) -> str:
    """The "nothing was saved" reply of `handle_configure_jira` (no verification)."""
    preamble = _connect_preamble(malformed_url, had_credentials)
    where = f" for `{host}`" if host else ""
    return (
        preamble + f"Jira access{where} no longer uses an API token stored on this "
        "machine. It runs through **your own Atlassian MCP connection** "
        "(OAuth, in your editor), so there is nothing to copy, nothing to "
        "rotate, and your own Jira permissions apply.\n\n"
        + connect_steps()
        + "\n\n"
        + verify_directive()
    )


def _jira_config_hint(url: str) -> str:
    """Deprecated shim -- always returns "".

    Until 2026-08-01 this produced one-time-setup instructions for the
    JIRA_EMAIL / JIRA_API_TOKEN pair. Those credentials no longer exist: Jira is
    read through the calling agent's own Atlassian MCP connection, so there is
    nothing to configure here and no hint worth giving. The SINGLE source of
    actionable guidance is tools/jira_mcp.build_fetch_directive /
    not_connected_message, which the fetch itself returns -- two competing
    messages for the same failure is precisely what confused testers before.

    Kept (not deleted) so both call sites in ground_and_gate stay structurally
    identical to the server path they were converged from; both now fall
    through. Never raises.
    """
    return ""

"""Moved from handlers (refactor P2): jira. Facade over the jira_* leaves."""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

from config.settings_guard import never_raise
from handlers.jira_config import (  # noqa: F401
    ConfigureJiraArgs,
    _audit_configure_jira,
    _connect_preamble,
    _jira_connect_reply,
    handle_configure_jira,
)
from handlers.jira_config import _jira_config_hint as jira_config_hint  # noqa: F401
from handlers.jira_hosts import (  # noqa: F401
    _jira_token_steps,
    _looks_like_jira_host,
    _safe_host,
    _skip_ui_extraction,
)
from handlers.jira_stage_assemble import (  # noqa: F401
    _STAGE_TOKEN_EXTRA_CHARS,
    _add_optional_staged_parts,
    _assemble_staged_jira_payload,
    _is_stage_token_shaped,
    _no_issue_staged_message,
    _resolve_jira_content_json,
    _safe_stage_token,
    _used_bridge_note,
)
from handlers.jira_stage_tray import (  # noqa: F401
    _JIRA_STAGE_PART_MAX_BYTES,
    _JIRA_STAGE_PARTS,
    _JIRA_STAGE_TRAY,
    _JIRA_STAGE_TRAY_MAX,
    _JIRA_STAGE_TRAY_TTL_S,
    _stage_refusal,
    _sweep_jira_stage_tray,
    handle_stage_jira,
)
from handlers.jira_text import _echoed_jira_host, _has_control_chars  # noqa: F401
from handlers.jira_verify import (  # noqa: F401
    _PERSISTABLE,
    _verdict_unrecorded_message,
    _verified_jira_reply,
)
from tools.jira_fetcher import verify_jira_access

logger = logging.getLogger("tools.mcp_handlers")


# Jira access pre-flight -- NETWORK-FREE since 2026-08-01.
#
# The server no longer holds Jira credentials, so there is nothing here it can
# verify: whether Jira is reachable is a property of the CALLING AGENT's
# Atlassian MCP connection, which a stdio subprocess cannot observe. The probe
# call is kept -- it is now tools/jira_mcp.verify_jira_access, which makes no
# HTTP request -- so this hook, its call sites and the tests that stub it keep
# their shape, and the happy path returns None so grounding proceeds to the
# fetch, where build_fetch_directive / not_connected_message give the ONE
# actionable message.
#
# The credential-elicitation loop is gone with the credentials: there is
# nothing a tester can type here that would help, and asking would be a dead
# end. The old ask_text / choose / progress hooks were unused and are gone.
#
# Returns None to proceed, or a markdown string to short-circuit. Never raises.
async def jira_preflight(url: str) -> Optional[str]:
    """Jira access pre-flight -- NETWORK-FREE since 2026-08-01."""
    if not _looks_like_jira_host(url):
        return None
    with never_raise(
        logger, "Jira pre-flight failed -- proceeding to the fetch", exc_info=True
    ):
        probe = await verify_jira_access()
        if probe.get("ok"):
            return None
        # A not-ok verdict can now only come from a test double or a future
        # replacement probe. Surface the connection steps rather than silently
        # continuing into a fetch that cannot succeed.
        return _jira_token_steps(
            (urlparse(url).hostname or "").lower(),
            verify_error=str(probe.get("error") or ""),
        )
    return None

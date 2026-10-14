"""Where an export lands and what it is called (moved out of handlers.export)."""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings_guard import never_raise
from handlers import _common
from tools import settings_port
from tools.jira_mcp import issue_key_from_url

logger = logging.getLogger("tools.mcp_handlers")


# ``settings.qa_export_dir`` as an ABSOLUTE path.
#
# The configured default ("data/exports") is RELATIVE, so it used to resolve
# against whatever working directory the MCP client happened to launch the
# server with: the same install therefore printed a different path in Claude
# Desktop, Claude Code and Cursor, and a non-technical tester could not find
# their own deliverable. Anchor a relative value to the install root instead.
#
# "" (the legacy secure-temp behavior) stays "". Never raises -- a failure
# degrades to the configured value as-is, which is exactly today's behavior.
def _resolved_export_dir() -> str:
    """``settings.qa_export_dir`` as an ABSOLUTE path."""
    with never_raise(
        logger, "resolving qa_export_dir failed -- using it as-is", exc_info=True
    ):
        raw = (settings_port.get().qa_export_dir or "").strip()
        if not raw:
            return ""
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = _common.INSTALL_ROOT / path
        return str(path)
    return (settings_port.get().qa_export_dir or "").strip()


# A Jira issue key for use inside an export FILENAME, or "".
#
# F11 (2026-08-15): three suites exported into one folder were
# ``qa_test_cases_<suite_id>_<stamp>.xlsx`` and nothing else, so a tester
# holding three tickets had to open all three to tell them apart.
#
# FILESYSTEM SAFETY is the whole reason this goes through
# ``jira_mcp.issue_key_from_url`` rather than any part of the raw URL: that
# helper is regex-gated to ``[A-Z][A-Z0-9]*-[0-9]+`` (``_valid_issue_key``),
# so the value spliced into a path cannot contain a separator, a traversal
# sequence, a NUL, or anything else needing escaping -- it is re-checked here
# anyway rather than trusted, because this function's output becomes a path.
# Returns "" for a non-Jira source, which restores the previous filename
# byte-for-byte. Never raises.
def _export_ticket_slug(source_url: str) -> str:
    """A Jira issue key for use inside an export FILENAME, or ""."""
    with never_raise(logger, "export ticket slug failed", exc_info=True):
        key = str(issue_key_from_url(str(source_url or "")) or "").strip()
        if not key or len(key) > 32:
            return ""
        if not all(ch.isalnum() or ch == "-" for ch in key):
            return ""
        return key
    return ""

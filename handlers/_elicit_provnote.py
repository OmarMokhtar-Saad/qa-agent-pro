"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise

logger = logging.getLogger("tools.mcp_handlers")


def _contributors(provenance: dict) -> list:
    """Every contributing client, or the solo client when none were recorded."""
    _clients = list(provenance.get("contributing_clients") or [])
    if not _clients:
        _clients = [str(provenance.get("client_name") or "unknown")]
    return _clients


# The A3/A4 warn-only banner: "" whenever every contributor is a known
# editor host, else a loud "> warning" line naming every unrecognised
# client. Ships ON, no flag (A3 owner decision) -- this NEVER gates a
# return; the caller's submit always succeeds regardless of what this
# returns. Reused for both the per-call ``qa_submit_category`` reply and
# the finalize ``qa_submit_suite`` reply/session-provenance section, so
# the two never drift. Never raises.
def unrecognized_client_note(provenance: dict, *, context: str) -> str:
    with never_raise(logger, "unrecognized_client_note failed", exc_info=True):
        provenance = provenance or {}
        if provenance.get("known_editor"):
            return ""
        _clients = _contributors(provenance)
        _label = ", ".join(f"`{c}`" for c in _clients if c) or "`unknown`"
        _scope = "This category submission" if context == "category" else "This suite"
        return (
            "> \u26a0\ufe0f **" + _scope + " was recorded from an MCP client "
            "this server does not recognise** (" + _label + "). If you did "
            "not expect that, a process may be calling this server's tools "
            "outside your editor's own MCP session. Submission was NOT "
            "blocked -- this is a disclosure, not a refusal.\n\n"
        )
    return ""


# The Generation Notes workbook row text for session provenance -- ""
# when every contributor is a known editor host (the sheet gets no row),
# else the same fact the chat warning discloses, phrased for a cell the
# tester reads later rather than mid-chat. Never raises.
def _provenance_gen_note_detail(provenance: dict) -> str:
    with never_raise(logger, "_provenance_gen_note_detail failed", exc_info=True):
        provenance = provenance or {}
        if provenance.get("known_editor"):
            return ""
        _clients = _contributors(provenance)
        _label = ", ".join(_clients) or "unknown"
        return (
            "This suite was recorded from an MCP client this server does "
            f"not recognise ({_label}). If this was not expected, a process "
            "may be calling this server's tools outside the editor's own MCP "
            "session."
        )
    return ""

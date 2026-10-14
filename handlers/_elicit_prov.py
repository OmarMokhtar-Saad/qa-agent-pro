"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers._elicit_host import host

logger = logging.getLogger("tools.mcp_handlers")


# --------------------------------------------------------------------------- #
# v1.97.0 cursor-hardening (item 3b/6a/6b): provenance state, kept SEPARATE
# from elicit_client_current so elicitation gating and provenance can evolve
# independently.
# --------------------------------------------------------------------------- #
_provenance_client = {"name": "", "version": ""}


# The documented hosts, kept in step with jira_mcp._detect_client_key's
# four clients (cursor / gemini-cli / claude-desktop / claude-code);
# "claude" covers the last two. Matched as a case-insensitive substring
# so a versioned or vendor-prefixed clientInfo.name still matches.
_KNOWN_EDITOR_SUBSTRINGS = ("cursor", "claude", "gemini")


def note_provenance_client(name, version: str = "") -> None:
    """Record which client (name + version) this process is serving, for
    stamping onto every saved suite. Mirrors note_elicit_client's shape.
    Never raises."""
    with never_raise(logger, "provenance client note failed", exc_info=True):
        _provenance_client["name"] = str(name or "").strip()
        _provenance_client["version"] = str(version or "").strip()


def _known_editor_host(name: str) -> bool:
    """True when name matches one of the documented hosts. Empty or
    unrecognized -- e.g. a side-channel process's own spawned
    mcp.ClientSession -- is False. Never raises."""
    with never_raise():
        low = str(name or "").strip().lower()
        if not low:
            return False
        return any(sub in low for sub in _KNOWN_EDITOR_SUBSTRINGS)
    return False


# {"dispatch", "client_name", "client_version", "known_editor"} for the
# suite about to be saved. Reaching this at all already proves
# dispatch_guard.require_dispatched passed for THIS call -- a guard-less
# attempt raises before any handler body runs -- so every suite saved
# through this path carries "dispatch": "mcp_tracked". Never raises.
def dispatch_provenance() -> dict:
    with never_raise(logger, "_dispatch_provenance failed", exc_info=True):
        name = _provenance_client.get("name", "")
        version = _provenance_client.get("version", "")
        return {
            "dispatch": "mcp_tracked",
            "client_name": name,
            "client_version": version,
            "known_editor": _known_editor_host(name),
        }
    return {
        "dispatch": "mcp_tracked",
        "client_name": "",
        "client_version": "",
        "known_editor": False,
    }


# Union of every ``qa_submit_category`` row's OWN client_name (Step 2
# stamps it into ``row["payload"]["client_name"]``) with the finalizing
# call's own client. A2/N2, the headline fix: before this,
# ``qa_submit_suite`` stamped ``known_editor`` from ONLY the finalizing
# process, so eight categories submitted by an unrecognised side-channel
# client and finalized by a known editor saved ``known_editor: True`` and
# dropped every contributor on the floor. ``known_editor`` is True here
# only when EVERY contributor (each row's client, plus this call's own) is
# a known editor host. Never raises -- any failure degrades to the single
# finalizing client, the pre-fix behaviour.
def union_provenance(rows: "list | None") -> dict:
    with never_raise(logger, "union_provenance failed", exc_info=True):
        _base = host()._dispatch_provenance()
        _final_name = str(_base.get("client_name") or "").strip()
        _clients = _union_clients(rows, _final_name)
        _known = (
            all(_known_editor_host(c) for c in _clients)
            if _clients
            else bool(_base.get("known_editor"))
        )
        return {
            "dispatch": _base.get("dispatch", "mcp_tracked"),
            "client_name": _final_name,
            "client_version": _base.get("client_version", ""),
            "known_editor": bool(_known),
            "contributing_clients": _clients,
        }
    return host()._dispatch_provenance()


def _union_clients(rows: "list | None", final_name: str) -> list:
    """Each row's own client name in first-seen order, then the finalizing client."""
    clients: list = []
    for row in rows or []:
        payload = (row or {}).get("payload") or {}
        name = str(payload.get("client_name") or "").strip()
        if name and name not in clients:
            clients.append(name)
    if final_name and final_name not in clients:
        clients.append(final_name)
    return clients

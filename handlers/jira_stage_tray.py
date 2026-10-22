"""Per-fetch Jira staging tray: the tray, its sweep and the qa_stage_jira handler."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools import clock

logger = logging.getLogger("tools.mcp_handlers")

# --------------------------------------------------------------------------- #
# v1.97.0 cursor-hardening (item 1): per-fetch Jira staging tray. Mirrors the
# _CAPTURE_TRAY shape exactly -- a token minted before the consuming call
# exists (by tools.jira_mcp.build_fetch_directive), redeemed once
# qa_prepare_test_cases/qa_generate_test_cases arrives with the same token,
# swept on every touch. Keeps a single raw Jira fetch result from ever having
# to survive inside one host-authored jira_content_json blob.
# --------------------------------------------------------------------------- #
_JIRA_STAGE_TRAY: dict = {}
_JIRA_STAGE_TRAY_TTL_S = 1800
_JIRA_STAGE_TRAY_MAX = 24
_JIRA_STAGE_PART_MAX_BYTES = 20 * 1024
_JIRA_STAGE_PARTS = ("issue", "parent", "siblings")


def _sweep_jira_stage_tray() -> None:
    """Evict stage-tray entries older than TTL or beyond MAX count (oldest
    first). Mirrors _sweep_capture_tray. Never raises."""
    with never_raise(logger, "_sweep_jira_stage_tray failed", exc_info=True):
        now = clock.now()
        for token, entry in list(_JIRA_STAGE_TRAY.items()):
            if now - float(entry.get("_ts") or 0) > _JIRA_STAGE_TRAY_TTL_S:
                _JIRA_STAGE_TRAY.pop(token, None)
        overflow = len(_JIRA_STAGE_TRAY) - _JIRA_STAGE_TRAY_MAX
        if overflow > 0:
            oldest = sorted(
                _JIRA_STAGE_TRAY.items(), key=lambda kv: kv[1].get("_ts") or 0
            )
            for token, _ in oldest[:overflow]:
                _JIRA_STAGE_TRAY.pop(token, None)


def _stage_refusal(token: str, part: str, json_text: str) -> str:
    """The refusal text for a bad stage call, or "" when it is acceptable."""
    if not token:
        return "No `stage_token` given -- call the fetch directive first."
    if part not in _JIRA_STAGE_PARTS:
        return (
            f"Unknown `part={part!r}` -- expected one of "
            f"{', '.join(_JIRA_STAGE_PARTS)}."
        )
    if len(str(json_text or "").encode("utf-8", "ignore")) > _JIRA_STAGE_PART_MAX_BYTES:
        return (
            "That staged part is too large to accept (over "
            f"{_JIRA_STAGE_PART_MAX_BYTES // 1024} KB). Save the raw fetch "
            "result to a file and call `qa_stage_jira` with `path=<absolute file>` "
            "instead of `json` -- I read and trim it myself."
        )
    return ""


def _store_stage_part(token: str, part: str, json_text: str) -> None:
    entry = _JIRA_STAGE_TRAY.setdefault(token, {})
    entry[part] = str(json_text or "")
    entry["_ts"] = clock.now()


async def handle_stage_jira(stage_token: str, part: str, json_text: str) -> str:
    """Stage ONE raw Jira fetch result (issue/parent/siblings) under
    stage_token, redeemed later by _assemble_staged_jira_payload. Treats
    json_text as UNTRUSTED, exactly like jira_content_json -- stored as-is,
    parsed only once, at assembly time. Never raises."""
    with never_raise(logger, "handle_stage_jira failed", exc_info=True):
        _sweep_jira_stage_tray()
        token = str(stage_token or "").strip()
        refusal = _stage_refusal(token, part, json_text)
        if refusal:
            return refusal
        _store_stage_part(token, part, json_text)
        _sweep_jira_stage_tray()
        return (
            f"Staged `{part}` part for this fetch. Stage the remaining parts, "
            "then call `qa_prepare_test_cases` with the same `stage_token`."
        )
    return "Could not stage that part -- re-fetch and try again."

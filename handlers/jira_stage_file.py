"""Stage a Jira fetch result from a file the host saved (big tickets, E-12)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from config.settings_guard import never_raise
from handlers import jira_stage_tray as tray

logger = logging.getLogger("tools.mcp_handlers")

_FILE_MAX_BYTES = 8 * 1024 * 1024
_STAGED_MAX_BYTES = 200 * 1024
_COMMENTS_KEPT = 20
_COMMENT_CHARS = 2000
_ATTACHMENT_KEYS = ("id", "filename", "mimeType", "size")


def _slim_attachments(fields: dict) -> None:
    """Keep only the identifying metadata of each attachment."""
    items = fields.get("attachment")
    if isinstance(items, list):
        fields["attachment"] = [
            {k: a[k] for k in _ATTACHMENT_KEYS if k in a}
            for a in items
            if isinstance(a, dict)
        ]


def _slim_comments(fields: dict) -> None:
    """Keep the newest comments with bounded bodies."""
    block = fields.get("comment")
    items = block.get("comments") if isinstance(block, dict) else None
    if not isinstance(block, dict) or not isinstance(items, list):
        return
    for c in items:
        if isinstance(c, dict) and isinstance(c.get("body"), str):
            c["body"] = c["body"][:_COMMENT_CHARS]
    block["comments"] = items[-_COMMENTS_KEPT:]


def _trim_issue(data: dict) -> str:
    """Drop bulky fields the test-case flow does not need; keep fields.updated."""
    fields = data["fields"]
    _slim_attachments(fields)
    _slim_comments(fields)
    return json.dumps(data, ensure_ascii=False)


def _read_capped(path: str) -> tuple[bytes | None, str]:
    """(file bytes, refusal): one open, at most the cap plus one byte read."""
    real = Path(os.path.expanduser(path)).resolve()
    if not path or not real.is_file():
        return None, (
            "That `path` is not a regular file -- save the fetch result to "
            "a file and pass its absolute path."
        )
    try:
        with real.open("rb") as handle:
            raw = handle.read(_FILE_MAX_BYTES + 1)
    except OSError as exc:
        return None, f"I could not read that file: {exc.strerror or exc}."
    if len(raw) > _FILE_MAX_BYTES:
        return None, "That file is over 8 MB -- it is not a Jira fetch result."
    return raw, ""


def _load_json(path: str) -> tuple[dict | None, str]:
    """(parsed Jira issue object, refusal). Path must be a bounded regular file."""
    raw, refusal = _read_capped(path)
    if raw is None:
        return None, refusal
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return (
            None,
            "That file is not valid JSON -- save the raw fetch result unchanged.",
        )
    if not isinstance(data, dict) or not isinstance(data.get("fields"), dict):
        return (
            None,
            "That file is not a Jira issue (no `fields` object) -- save the raw getJiraIssue result.",
        )
    return data, ""


def _stage_text(token: str, part: str, data: dict) -> str:
    """Store the trimmed issue, or say it is still too big."""
    text = _trim_issue(data)
    if len(text.encode("utf-8", "ignore")) > _STAGED_MAX_BYTES:
        return (
            "That ticket is still over 200 KB after trimming (comments cut to "
            "the newest 20, attachments to metadata). Re-fetch it without the "
            "`comment` field, save that result to a file and stage it again."
        )
    tray._store_stage_part(token, part, text)
    note = ""
    if not isinstance(data["fields"].get("updated"), str):
        note = (
            " Note: that file has no `fields.updated`, so freshness cannot "
            "be shown -- request the `updated` field."
        )
    return (
        f"Staged `{part}` part from the file. Stage the remaining parts, then "
        f"call `qa_prepare_test_cases` with the same `stage_token`.{note}"
    )


async def handle_stage_jira_file(stage_token: str, part: str, path: str) -> str:
    """Stage `part` from a saved raw fetch result; the server trims it. Never raises."""
    with never_raise(logger, "handle_stage_jira_file failed", exc_info=True):
        token = str(stage_token or "").strip()
        refusal = tray._stage_refusal(token, part, "")
        if refusal:
            return refusal
        tray._sweep_jira_stage_tray()
        data, refusal = await asyncio.to_thread(_load_json, str(path or ""))
        if data is None:
            return refusal
        return _stage_text(token, part, data)
    return "Could not stage that file -- save the fetch result again and retry."

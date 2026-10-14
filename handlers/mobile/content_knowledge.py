"""``qa_mobile_notes`` and ``qa_mobile_knowledge``: one app's saved notes and knowledge."""

from __future__ import annotations

import asyncio
import logging

from handlers import _common, mobile_gate
from handlers.mobile import step as mobile_step
from handlers.mobile.content_requests import KnowledgeRequest, NotesRequest
from tools.mobile import knowledge_tool

logger = logging.getLogger("tools.mcp_handlers")


async def _notes_verb(verb: str, target: tuple, req: NotesRequest) -> str:
    """Run the validated notes *verb* on ``target = (knowledge_tool, package, run id)``."""
    tool, pkg, rid = target
    if verb == "list":
        # SQLite with a busy timeout: off the event loop, like the note intake.
        return await asyncio.to_thread(tool.notes_list, pkg)
    if not req.note_id:
        return "⚠️ `retire` needs `note_id`."
    return await asyncio.to_thread(
        tool.notes_retire, tool.Retire(pkg, req.note_id, str(req.reason or ""), rid)
    )


# ``qa_mobile_notes``: list or retire the notes saved about one app.
#
# A thin alias kept for one release; ``qa_mobile_knowledge`` is the full tool
# (``handle_mobile_knowledge``). Output is unchanged. Never raises.
async def handle_mobile_notes(req: NotesRequest) -> str:
    """``qa_mobile_notes``: list or retire the notes saved about one app."""
    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        verb = str(req.action or "list").strip().lower()
        if verb not in ("list", "retire"):
            return "⚠️ `action` must be `list` or `retire`."
        pkg, rid, refusal = mobile_step._mobile_note_target(req.run_id, req.package)
        if refusal:
            return "⚠️ " + refusal + "."
        return await _notes_verb(verb, (knowledge_tool, pkg, rid), req)
    except Exception:
        logger.exception("mobile notes failed")
        return "⚠️ The notes store could not be read."


async def _knowledge_reply(req: KnowledgeRequest, pkg: str, tool) -> str:
    """``tools.mobile.knowledge_tool.handle`` for *req*, off the event loop."""
    return await asyncio.to_thread(
        tool.handle, tool.Ask(req.action, pkg, req.item_id, req.run_id, req.data)
    )


# ``qa_mobile_knowledge``: one app's saved knowledge. A shim over
# ``tools.mobile.knowledge_tool.handle``. Never raises.
async def handle_mobile_knowledge(req: KnowledgeRequest) -> str:
    """``qa_mobile_knowledge``: one app's saved knowledge."""
    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        pkg, _rid, refusal = mobile_step._mobile_note_target(req.run_id, req.package)
        if refusal:
            return "⚠️ " + refusal + "."
        return await _knowledge_reply(req, pkg, knowledge_tool)
    except Exception:
        logger.exception("mobile knowledge failed")
        return "⚠️ The knowledge store could not be read."

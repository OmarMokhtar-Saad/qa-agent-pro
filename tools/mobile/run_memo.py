"""Per-run memos of what a chat was already sent: the briefing and the last screen.

:func:`briefed_session` is the session that already holds this run's static
packet block, or ``""``, as ``{"error", "content": "<session id>"}``. The elided
fields are identical on every packet of a run in every lane, and re-sending
them on all thirty turns costs a tester about 53,000 tokens of context for
bytes the model already has. They go to a chat ONCE and this is the record.

Why the SESSION and not the run: a run resumed in a new chat, or taken over
from another one, is a model that has never seen the block. Never raises, and
an unreadable marker reads as "not briefed" -- the safe direction, per
docs/RETIRED_CAPABILITIES.md section 5, constraint 6.

:func:`mark_briefed` records that *session_id* has been handed this run's
static block.

:func:`note_schema` says whether the briefed chat already holds the response
schema (and records it if not): the schema goes out whole once per run.

:func:`clear_briefing` forgets who was briefed, so the NEXT packet carries the
block again. Called when a submitted script could not be parsed: that is the
observable signal that the model no longer has the block -- a compacted chat,
or one that never received it -- and re-arming here is what makes the omission
self-healing rather than a trap for the rest of the run.

The screen-block memo (:func:`last_screen`, :func:`mark_screen`,
:func:`clear_screen`) lives in ``run_memo_screen`` and is re-exported here.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path

from tools.mobile import run_consts, run_io
from tools.mobile.run_memo_screen import (  # noqa: F401
    ScreenMemo,
    _screen_memo_path,
    clear_screen,
    last_screen,
    mark_screen,
    shown_ids,
)

logger = logging.getLogger("tools.mobile.run_store")

# Chat scope: a chat (token) that already holds a static block needs no second
# copy when it starts another run. In-process and bounded; a restart or a
# missing entry reads as "not briefed", which sends the block whole.
CHAT_BRIEFED_MAX = 64
_CHAT_BRIEFED: OrderedDict[str, tuple[str, str]] = OrderedDict()


def chat_has_block(token: str, static_hash: str, run_id: str) -> bool:
    """Whether chat *token* got the block *static_hash* in an EARLIER run.

    The same run never counts: an unreadable run marker must repeat the block.
    """
    seen = _CHAT_BRIEFED.get(token) if token and static_hash else None
    return bool(seen and seen[0] == static_hash and seen[1] != run_id)


def _note_chat_block(token: str, static_hash: str, run_id: str) -> None:
    if not token or not static_hash:
        return
    _CHAT_BRIEFED[token] = (static_hash, run_id)
    _CHAT_BRIEFED.move_to_end(token)
    while len(_CHAT_BRIEFED) > CHAT_BRIEFED_MAX:
        _CHAT_BRIEFED.popitem(last=False)


def _briefing_path(run_id: str) -> Path:
    return run_io.run_path(run_id) / run_consts.BRIEFING_FILE


def briefed_session(run_id: str) -> dict:
    """The session that already holds this run's static packet block, or ``""``."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": ""}
        body = run_io._read_json(_briefing_path(run_id))
        if not isinstance(body, dict):
            return {"error": None, "content": ""}
        return {
            "error": None,
            "content": str(body.get("session_id") or ""),
            "static_hash": str(body.get("static_hash") or ""),
        }
    except Exception as exc:
        logger.exception("mobile.run_store.briefed_session failed")
        return {"error": str(exc), "content": ""}


def _kept_hash(run_id: str, session_id: str, static_hash: str) -> str:
    """*static_hash*, or the one this same chat was briefed with when empty."""
    if static_hash:
        return str(static_hash)
    seen = briefed_session(run_id)
    if seen.get("content") == str(session_id):
        return str(seen.get("static_hash") or "")
    return ""


def mark_briefed(run_id: str, session_id: str, static_hash: str = "") -> dict:
    """Record that *session_id* has been handed this run's static block.

    *static_hash* is the short id of that block, so a changed block is re-sent.
    A packet with no static block (empty hash) keeps the hash this same chat
    was already briefed with, so it cannot make the next real block look new.
    """
    try:
        if not run_io.valid_run_id(run_id) or not str(session_id or "").strip():
            return {"error": "Invalid run id or session.", "content": None}
        body = {
            "session_id": str(session_id),
            "static_hash": _kept_hash(run_id, session_id, static_hash),
        }
        run_io._write_json(_briefing_path(run_id), body)
        _note_chat_block(str(session_id), body["static_hash"], run_id)
        return {"error": None, "content": {"session_id": str(session_id)}}
    except Exception as exc:
        logger.exception("mobile.run_store.mark_briefed failed")
        return {"error": str(exc), "content": None}


def note_schema(run_id: str, schema_hash: str) -> bool:
    """Whether the briefed chat already holds this response schema; else record it.

    Lives in the briefing record, so a re-briefing (new chat, new block, a
    cleared briefing) also sends the schema whole. Never raises; any failure
    reads as "not seen", which sends the schema whole.
    """
    try:
        body = run_io._read_json(_briefing_path(run_id))
        if not isinstance(body, dict) or not body.get("session_id"):
            return False
        if body.get("schema_hash") == schema_hash:
            return True
        run_io._write_json(_briefing_path(run_id), {**body, "schema_hash": schema_hash})
    except Exception:
        logger.exception("mobile.run_store.note_schema failed")
    return False


def clear_briefing(run_id: str) -> dict:
    """Forget who was briefed, so the NEXT packet carries the block again."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        _CHAT_BRIEFED.pop(str(briefed_session(run_id).get("content") or ""), None)
        _briefing_path(run_id).unlink(missing_ok=True)
        return {"error": None, "content": {"cleared": True}}
    except Exception as exc:
        logger.exception("mobile.run_store.clear_briefing failed")
        return {"error": str(exc), "content": None}

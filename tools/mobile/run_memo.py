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

:func:`clear_briefing` forgets who was briefed, so the NEXT packet carries the
block again. Called when a submitted script could not be parsed: that is the
observable signal that the model no longer has the block -- a compacted chat,
or one that never received it -- and re-arming here is what makes the omission
self-healing rather than a trap for the rest of the run.

:func:`last_screen` is the screen block this run last sent, as ``{session,
fingerprint, position}``. The briefing memo's pattern, for the element list: a
packet whose screen is byte-identical to the one the SAME chat was just handed
says so in one line instead of repeating it. Never raises; an unreadable memo
reads as "nothing sent", which makes the next packet go out whole -- the safe
direction.

:func:`mark_screen` records the screen block *session_id* was just handed, and
where. :func:`clear_screen` forgets the last screen sent, so the NEXT packet
carries it whole; called wherever :func:`clear_briefing` is: the same signal
says the model may no longer hold what it was sent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

from tools.mobile import run_consts, run_io

logger = logging.getLogger("tools.mobile.run_store")


class ScreenMemo(NamedTuple):
    session: str
    fingerprint: str
    position: str


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
        return {"error": None, "content": str(body.get("session_id") or "")}
    except Exception as exc:
        logger.exception("mobile.run_store.briefed_session failed")
        return {"error": str(exc), "content": ""}


def mark_briefed(run_id: str, session_id: str) -> dict:
    """Record that *session_id* has been handed this run's static block."""
    try:
        if not run_io.valid_run_id(run_id) or not str(session_id or "").strip():
            return {"error": "Invalid run id or session.", "content": None}
        run_io._write_json(_briefing_path(run_id), {"session_id": str(session_id)})
        return {"error": None, "content": {"session_id": str(session_id)}}
    except Exception as exc:
        logger.exception("mobile.run_store.mark_briefed failed")
        return {"error": str(exc), "content": None}


def clear_briefing(run_id: str) -> dict:
    """Forget who was briefed, so the NEXT packet carries the block again."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        _briefing_path(run_id).unlink(missing_ok=True)
        return {"error": None, "content": {"cleared": True}}
    except Exception as exc:
        logger.exception("mobile.run_store.clear_briefing failed")
        return {"error": str(exc), "content": None}


def _screen_memo_path(run_id: str) -> Path:
    return run_io.run_path(run_id) / run_consts.SCREEN_MEMO_FILE


def _memo_fields(body: dict) -> dict:
    return {
        key: str(body.get(key) or "") for key in ("session", "fingerprint", "position")
    }


def last_screen(run_id: str) -> dict:
    """The screen block this run last sent, as ``{session, fingerprint, position}``."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": {}}
        body = run_io._read_json(_screen_memo_path(run_id))
        if not isinstance(body, dict):
            return {"error": None, "content": {}}
        return {"error": None, "content": _memo_fields(body)}
    except Exception as exc:
        logger.exception("mobile.run_store.last_screen failed")
        return {"error": str(exc), "content": {}}


def mark_screen(run_id: str, screen: ScreenMemo) -> dict:
    """Record the screen block *screen.session* was just handed, and where."""
    try:
        if not run_io.valid_run_id(run_id) or not str(screen.session or "").strip():
            return {"error": "Invalid run id or session.", "content": None}
        memo = {
            "session": str(screen.session),
            "fingerprint": str(screen.fingerprint or ""),
            "position": str(screen.position or ""),
        }
        run_io._write_json(_screen_memo_path(run_id), memo)
        return {"error": None, "content": memo}
    except Exception as exc:
        logger.exception("mobile.run_store.mark_screen failed")
        return {"error": str(exc), "content": None}


# Called wherever :func:`clear_briefing` is: the same signal says the model
# may no longer hold what it was sent.
def clear_screen(run_id: str) -> dict:
    """Forget the last screen sent, so the NEXT packet carries it whole."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        _screen_memo_path(run_id).unlink(missing_ok=True)
        return {"error": None, "content": {"cleared": True}}
    except Exception as exc:
        logger.exception("mobile.run_store.clear_screen failed")
        return {"error": str(exc), "content": None}

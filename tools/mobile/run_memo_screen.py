"""The screen-block memo of a run: what screen a chat was last sent.

Split from ``run_memo`` (re-exported there). Never raises; an unreadable memo
reads as "nothing sent", so the next packet goes out whole.
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
    ids: tuple = ()


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


def shown_ids(run_id: str) -> tuple:
    """Element ids of the last screen block sent, in ``#n`` order. Never raises."""
    try:
        body = run_io._read_json(_screen_memo_path(run_id))
        ids = body.get("ids") if isinstance(body, dict) else None
        return tuple(str(x) for x in ids) if isinstance(ids, list) else ()
    except Exception:
        logger.exception("mobile.run_store.shown_ids failed")
        return ()


def mark_screen(run_id: str, screen: ScreenMemo) -> dict:
    """Record the screen block *screen.session* was just handed, and where."""
    try:
        if not run_io.valid_run_id(run_id) or not str(screen.session or "").strip():
            return {"error": "Invalid run id or session.", "content": None}
        memo = {
            "session": str(screen.session),
            "fingerprint": str(screen.fingerprint or ""),
            "position": str(screen.position or ""),
            "ids": [str(x) for x in screen.ids],
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
        kept = shown_ids(run_id)
        _screen_memo_path(run_id).unlink(missing_ok=True)
        if kept:
            run_io._write_json(_screen_memo_path(run_id), {"ids": list(kept)})
        return {"error": None, "content": {"cleared": True}}
    except Exception as exc:
        logger.exception("mobile.run_store.clear_screen failed")
        return {"error": str(exc), "content": None}

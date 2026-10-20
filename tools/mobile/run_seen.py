"""Which per-run packet fields the briefed chat already holds (by digest).

Same record and same fail-open direction as ``run_memo.note_schema``: it lives
in the briefing record, so a re-briefing (new chat, new block) clears it, and
any failure reads as "not seen", which sends the field whole.
"""

from __future__ import annotations

import logging

from tools.mobile import run_io
from tools.mobile.run_memo import _briefing_path

logger = logging.getLogger("tools.mobile.run_seen")


def note_seen(run_id: str, key: str, digest: str) -> bool:
    """Whether the briefed chat holds *key* with this *digest*; else record it."""
    try:
        path = _briefing_path(run_id)
        body = run_io._read_json(path)
        if not isinstance(body, dict) or not body.get("session_id"):
            return False
        raw = body.get("seen")
        seen = raw if isinstance(raw, dict) else {}
        if seen.get(key) == digest:
            return True
        run_io._write_json(path, {**body, "seen": {**seen, key: digest}})
    except Exception:
        logger.exception("mobile.run_seen.note_seen failed")
    return False

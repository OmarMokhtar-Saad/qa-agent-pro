"""Disclosure of in-flight (unfinished) preps so a host-mode run is resumable."""

from __future__ import annotations

import logging
import time
from typing import Any

from config.settings_guard import never_raise
from tools import prep_store

logger = logging.getLogger("agents.host_mode")


def _hhmm(value: Any) -> str:
    return time.strftime("%H:%M", time.localtime(float(value or 0)))


def _count_text(row: dict) -> str:
    staged = int(row.get("staged_count") or 0)
    expected = int(row.get("expected_count") or 0)
    return f"{staged}/{expected}" if expected else f"{staged}"


def _unfinished_line(pid: str, row: dict) -> str:
    started = _hhmm(row.get("created_at"))
    expires = _hhmm(row.get("expires_at"))
    return (
        f"> ⏳ Unfinished prep `{pid}` from {started}: {_count_text(row)} "
        f"category row(s) staged, expires ~{expires}. Resume with "
        "`qa_prep_status` + `qa_submit_category` (same prep_id), "
        "or ignore it -- it expires on its own."
    )


def _unfinished_lines(rows: list, exclude_prep_id: str) -> list[str]:
    lines = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        pid = str(r.get("prep_id") or "")
        if not pid or pid == exclude_prep_id:
            continue
        lines.append(_unfinished_line(pid, r))
    return lines


async def unfinished_preps_note(exclude_prep_id: str = "") -> str:
    """Markdown disclosure of in-flight preps (fetched worker packets or
    staged category rows) so an interrupted host-mode run is resumable
    instead of evaporating at TTL (2026-07-31 TICKET-5645 incident).

    DISCLOSURE ONLY -- never blocks anything. Returns "" when nothing
    qualifies or the store errors. The line prints the prep_id, which is the
    capability token for that prep. Times render as the server's local HH:MM.
    Never raises."""
    with never_raise(logger, "unfinished-preps disclosure failed", exc_info=True):
        res = await prep_store.list_unfinished_preps(limit=3)
        rows = res.get("content") or []
        lines = _unfinished_lines(rows, exclude_prep_id)
        return ("\n".join(lines) + "\n") if lines else ""
    return ""

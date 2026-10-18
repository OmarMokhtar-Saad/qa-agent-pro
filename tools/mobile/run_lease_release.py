"""Stamp ``released_at`` on a run's lease when the run ends or is aborted.

Without it ``lease.json`` keeps its last heartbeat forever, and a finished run
reads the same as a live one that stopped beating a moment ago.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings_guard import never_raise
from tools.mobile import run_io, run_lease_core

logger = logging.getLogger("tools.mobile.run_store")

_RESTAMPS = 2


def _releasable(current: object, session_id: str) -> bool:
    """A lease with a holder, and not one a different session now owns."""
    if not isinstance(current, dict) or not current.get("session_id"):
        return False
    return not session_id or current.get("session_id") == session_id


def _stamp(path: Path, current: dict, moment: float) -> None:
    """Write ``released_at``, then re-read: a heartbeat that rewrote the lease
    from its own copy in between dropped the stamp, so put it back (twice at most)."""
    for _ in range(_RESTAMPS):
        run_io._write_json(path, {**current, "released_at": moment})
        current = run_io._read_json(path) or current
        if current.get("released_at"):
            return


def mark_released(
    run_id: str, session_id: str = "", *, now: float | None = None
) -> bool:
    """Write ``released_at`` into the lease; ``True`` when it was written.

    Never raises. A lease held by a different session than *session_id* is left
    alone: that chat took the run over and still owns it.
    """
    with never_raise(logger, "mobile.run_lease.mark_released failed for %s", run_id):
        if not run_io.valid_run_id(run_id):
            return False
        path = run_lease_core._lease_path(run_id)
        current = run_io._read_json(path)
        if not _releasable(current, session_id) or current.get("released_at"):
            return False  # not ours, or already released: keep the first stamp
        _stamp(path, current, run_io._now(now))
        return True
    return False

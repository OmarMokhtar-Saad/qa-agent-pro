"""Record the verdict of a run that reached its own report (goal reached, every case done).

``finish_device`` only gives the device back; the one writer of
``manifest["final"]`` is ``run_finalize.finalize``. A run that ended by reaching
its goal never called it, so the idle timer later closed it ``unfinished`` /
``idle_timeout`` against its own Pass report and learning waited for that close.
:func:`record` writes the verdict the report states, at the report. Never raises.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import run_finalize, session_records
from tools.mobile.run_verdict import Verdict

logger = logging.getLogger("tools.mobile.session")

#: ``final.stop`` for a run whose own report closed it.
STOP_COMPLETED = "completed"
_WORDS = {"pass": "passed", "fail": "failed", "unverified": "not verified"}


def _reason(word: str, rows: list) -> str:
    done = sum(1 for r in rows if r.get("verdict"))
    return "run %s: %d of %d steps reached a verdict" % (
        _WORDS.get(word, word),
        done,
        len(rows),
    )


def record(run_id: str, resolved: Any, rows: Any) -> bool:
    """Finalize *run_id* with the verdict its report states. True when written."""
    listed = [r for r in list(rows or []) if isinstance(r, dict)]
    word = session_records.run_verdict(resolved, listed)
    verdict = Verdict(word, _reason(word, listed), ("run report",))
    try:
        written = run_finalize.finalize(run_id, verdict, stop=STOP_COMPLETED)
    except RuntimeError:  # finalize raises this; the report must still reach the tester
        logger.warning(
            "run_finalize_done: could not finalize %s", run_id, exc_info=True
        )
        return False
    if not written:
        logger.info(
            "run_finalize_done: %s already final, report verdict not recorded", run_id
        )
    return written

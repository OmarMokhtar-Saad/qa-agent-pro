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

from tools.mobile import render_tally, run_finalize, run_idle_words, session_records
from tools.mobile.run_verdict import Verdict

logger = logging.getLogger("tools.mobile.session")

#: ``final.stop`` for a run whose own report closed it.
STOP_COMPLETED = "completed"
_WORDS = {"pass": "passed", "fail": "failed", "unverified": "not verified"}


def _counts(rows: list) -> tuple[int, int]:
    """(failed, interrupted) turn counts."""
    failed = sum(1 for r in rows if r.get("status") == "failed")
    stopped = sum(1 for r in rows if r.get("status") == "abandoned")
    return failed, stopped


def _stumbled(rows: list) -> str:
    """``"9 failed and 1 interrupted"`` for turns that ended badly, or ``""``."""
    failed, stopped = _counts(rows)
    said = [f"{failed} failed"] if failed else []
    if stopped:
        said.append(f"{stopped} interrupted")
    return " and ".join(said)


def qualified_verdict(word: str, rows: list) -> str:
    """``"pass, with 5 failed and 3 interrupted turns"`` for a hurt pass, else *word*."""
    rows = [row for row in rows if isinstance(row, dict)]
    hurt = _stumbled(rows) if word == "pass" else ""
    if not hurt:
        return word
    return f"{word}, with {hurt} {'turn' if sum(_counts(rows)) == 1 else 'turns'}"


def _count_line(word: str, rows: list, done: int) -> str:
    """``run passed: 5 of 15 ...``; a pass with failed turns names them first."""
    hurt = _stumbled(rows) if word == "pass" else ""
    if hurt:
        return f"run passed, but {hurt} of {len(rows)} turns; {done} reached a verdict"
    return "run %s: %d of %d steps reached a verdict" % (
        _WORDS.get(word, word),
        done,
        len(rows),
    )


def _reason(word: str, rows: list) -> str:
    done = sum(1 for r in rows if r.get("verdict"))
    # A passed run is judged by its final done(pass): explore rows without a verdict
    # are stumbles on the way, not an unfinished run.
    undecided = 0 if word == "pass" else len(rows) - done
    said = run_idle_words.unjudged_line(
        len(rows), undecided, run_idle_words.problem_of(rows)
    )
    if said:
        return said
    return _count_line(word, rows, done)


def _hurt(word: str, rows: list) -> tuple[int, int, int]:
    """(failed, interrupted, unconfirmed inputs) of a passed run, else zeros."""
    if word != "pass":
        return 0, 0, 0
    return (*_counts(rows), render_tally.unconfirmed_inputs(rows))


def record(run_id: str, resolved: Any, rows: Any) -> bool:
    """Finalize *run_id* with the verdict its report states. True when written."""
    listed = [r for r in list(rows or []) if isinstance(r, dict)]
    word = session_records.run_verdict(resolved, listed)
    hurt = _hurt(word, listed)
    verdict = Verdict(word, _reason(word, listed), ("run report",), *hurt)
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

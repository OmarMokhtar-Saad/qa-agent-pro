"""The words a server-closed run carries: turns, actions, where it stopped, how to resume.

A *turn* is one committed exploratory case (``TC-nnn``); a turn holds several
device *actions* (its trace). The two are reported apart and labelled, so a run
that committed 3 turns never reads "after step 8".
"""

from __future__ import annotations

import time
from typing import NamedTuple

from tools.mobile import run_store


def resume_hint(run_id: str) -> str:
    """What to do next: a closed run takes no more steps, so start a new one."""
    return (
        f"Run {run_id} has ended and takes no more steps: start a new run "
        '(qa_mobile_test source="explore" with the same goal) to continue; '
        "qa_mobile_status shows this run's result."
    )


class Progress(NamedTuple):
    turns: int
    actions: int
    last: str
    run_id: str = ""
    undecided: int = 0
    problem: str = ""


def unjudged_line(total: int, undecided: int, problem: str) -> str:
    """The verdict clause of a run that ended with steps that never got one, or ``""``."""
    if not undecided:
        return ""
    line = f"unfinished: {undecided} of {total} steps did not reach a verdict"
    return f"{line}; last: {problem}" if problem else line


def problem_of(rows: list) -> str:
    """Why the LAST step without a verdict stopped: its reason, else its title."""
    open_rows = [c for c in rows if not c.get("verdict")]
    last = open_rows[-1] if open_rows else {}
    return " ".join(str(last.get("reason") or last.get("title") or "").split())[:200]


def progress_of(run_id: str) -> Progress:
    """Committed turns, device actions, and the last turn's title, as recorded."""
    cases = (run_store.list_cases(run_id) or {}).get("content") or []
    rows = [c for c in cases if isinstance(c, dict)]
    actions = sum(len(c.get("trace") or []) for c in rows)
    last = str(rows[-1].get("title") or rows[-1].get("tc_id") or "") if rows else ""
    undecided = sum(1 for c in rows if not c.get("verdict"))
    return Progress(len(rows), actions, last, run_id, undecided, problem_of(rows))


def _clock(now: float) -> str:
    return time.strftime("%H:%M", time.localtime(now))


def _done(p: Progress) -> str:
    return f"{p.turns} turn(s) committed ({p.actions} device actions)"


def _head(p: Progress) -> str:
    noun = "action" if p.actions == 1 else "actions"
    return f"after turn {p.turns} ({p.actions} {noun})"


def idle_reason(p: Progress, now: float) -> str:
    """Wording of a run the idle timer closed."""
    closed = f"closed by the server at {_clock(now)}"
    said = unjudged_line(p.turns, p.undecided, p.problem)
    lead = f"{said}. " if said else ""
    return f"{lead}went idle {_head(p)}; {closed}. {resume_hint(p.run_id)}"


def died_reason(p: Progress, now: float) -> str:
    """Wording of a run whose server process is gone."""
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    closed = f"closed on {day} at {_clock(now)}"
    return f"the server process ended {_head(p)}; {closed}. {resume_hint(p.run_id)}"


def orphaned_reason(p: Progress, now: float) -> str:
    """Wording of a run nobody drives any more although its server never died."""
    why = "owner process alive or its pid reused; run superseded or idle"
    return (
        f"left unattended {_head(p)} ({why}); "
        f"closed at {_clock(now)}. {resume_hint(p.run_id)}"
    )


def evidence_of(p: Progress) -> tuple:
    """What was done and where it stopped, as verdict evidence lines."""
    return (_done(p), "last turn: " + p.last) if p.last else (_done(p),)

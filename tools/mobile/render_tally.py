"""Typed-field tally and the note on cases that did not settle.

Tenth layer of the ``render_*`` split of ``render.py``.

``_typed_field_tally`` -- "N typed fields verified, M failed", naming each
failed field. Read from each case's own trace: a ``type`` whose outcome is
``ok`` landed (``replay.device_ops._judge_landed`` made every other verdict a
failure), and one that came back ``device_error`` or ``refused`` did not. Empty
when the run typed nothing, so a run that never touched a field is not told
about one.

``typed_field_tally`` -- public wrapper around :func:`_typed_field_tally`.
:func:`verdict_line` (the per-turn submit reply) and ``report.py``'s
``_meta_html`` (the finished-run report) both call this ONE name, so the field
count and the failed-field names can never drift between the chat reply and the
report page.

``_retry_note`` -- name the cases that did not settle, and say what happens
next.

2026-09-04: a live session started EIGHT runs in fourteen minutes, and it was
read as the chat model ignoring the guidance to resume. It was not. Resuming a
run whose cases have all reached a terminal verdict returns "finished" and the
report -- correctly, since `unverified` and `blocked` ARE terminal and the
scheduler must not re-serve them -- so a fresh run was the only move available,
and nothing said so. The model invented the next step, and invented the same
one eight times.

This does not add a retry path; re-attempting a case inside its own run is a
feature with lease, scheduler and report consequences, and is recorded as a
follow-up rather than improvised here. What it removes is the silence: a tester
(and a model) is told which cases did not settle and that another attempt is a
new run, which is the truth about this lane today.
"""

from __future__ import annotations

from typing import Any

from tools.mobile.session_explore_ids import EXPLORE_MODULE


def _type_steps(rows: list[dict]) -> list[tuple[str, str]]:
    """``(field, outcome)`` of every type or fill step, in trace order."""
    steps: list[tuple[str, str]] = []
    for row in rows:
        for item in list(row.get("trace") or []):
            action: Any = item.get("action") if isinstance(item, dict) else None
            if not isinstance(action, dict) or action.get("op") not in ("type", "fill"):
                continue
            name = str(action.get("field") or "") or "(unnamed field)"
            steps.append((name, str(item.get("outcome") or "")))
    return steps


def _typed_outcomes(rows: list[dict]) -> tuple[int, list[str]]:
    steps = _type_steps(rows)
    failed: list[str] = []
    for name, outcome in steps:
        if outcome == "ok" and name in failed:
            # A field that failed and was typed again successfully was retried.
            failed = [n for n in failed if n != name]
        elif outcome in ("device_error", "refused"):
            failed.append(name)
    verified = sum(1 for _, outcome in steps if outcome == "ok")
    return verified, failed


def _is_explore(rows: list[dict]) -> bool:
    """Whether every row is an explore turn (the Exploration module)."""
    return bool(rows) and all(r.get("module") == EXPLORE_MODULE for r in rows)


def _typed_field_tally(rows: list[dict], explore: bool = False) -> str:
    """ "N typed fields verified, M failed", naming each failed field."""
    verified, failed = _typed_outcomes(rows)
    if not verified and not failed:
        return ""
    # An explore turn that stumbled is a warning: the run's verdict is the final
    # done(pass), not every step. A suite case that failed still says "failed".
    word = "warnings" if explore or _is_explore(rows) else "failed"
    line = str(verified) + " typed fields verified, " + str(len(failed)) + " " + word
    if failed:
        line += ": " + ", ".join(repr(name[:60]) for name in failed[:12])
        if len(failed) > 12:
            line += " and others"
    return line + "\n\n"


def typed_field_tally(rows: list[dict], explore: bool = False) -> str:
    """Public wrapper; `explore` is True when the run's lane is explore."""
    return _typed_field_tally(rows, explore)


#: Verdicts that are TERMINAL but not an ANSWER. A pass or a fail settles the
#: case; these two say the case did not settle, and the tester's next move is
#: to run it again rather than to read the result.
INCONCLUSIVE_VERDICTS: frozenset = frozenset({"unverified", "blocked"})

_RETRY_TAIL = (
    " did not reach a pass or a fail.** `unverified` means the script"
    " asserted nothing, so nothing was checked; `blocked` means it could"
    " not get far enough to try. Neither is a result you can report.\n\n"
    "This run is complete and those cases will not be handed out again in"
    " it. To attempt one, start a NEW run scoped to it -- `qa_mobile_test`"
    " takes a `cases` filter, so you need not replay the ones that already"
    " settled -- and give the script an `assert` for what the case is"
    " supposed to show, because that is what turns an attempt into a"
    " verdict."
)


def _retry_note(rows: list, *, partial: bool) -> str:
    """Name the cases that did not settle, and say what happens next."""
    if partial:
        return ""
    unsettled = [
        str(row.get("tc_id") or "?")
        for row in rows
        if str(row.get("verdict") or "") in INCONCLUSIVE_VERDICTS
    ]
    if not unsettled:
        return ""
    names = ", ".join(unsettled[:12]) + (" and others" if len(unsettled) > 12 else "")
    return "\n\n**" + names + _RETRY_TAIL

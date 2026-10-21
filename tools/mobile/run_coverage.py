"""Verdict coverage: what counts as FINISHED and how much of a run reached it.

:data:`DONE_VERDICTS` is a verdict that means the case is FINISHED -- it will
not be handed out again. Defined here, at the layer that decides what "done"
means, and imported by `report.DONE_VERDICTS`; `case_runner` accepts exactly
these plus the empty string it normalises away. One definition, because the
last verdict to be added reached two of the three copies and hung the run.

:func:`_has_verdict` is whether ONE case record has reached a verdict. The same
predicate ``report._verdict_of(case) in DONE_VERDICTS`` evaluates to -- that
function returns the verdict when it is terminal and the STATUS otherwise, so a
case with ``verdict: ""`` and ``status: "done"`` is not done here either. A
test binds the two so they cannot drift.

:func:`verdict_coverage` returns ``{"lane", "total", "with_verdict",
"expected", "complete"}``.

THE producer of "how many of these reached a verdict". Five surfaces of the
HTML report and two blocks of the chat status reply used to answer that
question, and three of them answered a DIFFERENT one -- ``report._is_partial``,
which is a RUN-LIFECYCLE fact ("is this run still going"). On
``mrun-20260907-174354-758700`` a finished explore run was captioned "every
planned case reached a verdict" beside a hero reading "0 passed - 0 failed -
0 blocked". Two values, two names: ``partial`` keeps the lifecycle and this
carries coverage.

*expected* is the number of records ever MEANT to earn a verdict:

* the SUITE lane plans one verdict per case, so it is the planned total or
  the number of checkpoints, whichever is larger -- a run may checkpoint
  more than an older manifest declares;
* the EXPLORE lane earns ONE verdict for the whole run, on the turn that
  judges the goal. A turn has no expected result, so counting 17
  exploratory turns as 17 missing verdicts reports a gap that does not
  exist. ``expected`` is 1 once ``explore_stop.display_stop`` calls the
  run stopped, and 0 before that. NOT ``explore.stop``, which is one of that
  producer's three clauses: a run stopped by its deadline or its turn budget
  never sets the field, so this counted it as still running and the page
  printed "finished -- stopped: deadline_reached" and "this one has not
  stopped yet" in a single sentence.

Never raises: a junk ``total`` reads as 0.

:func:`coverage_phrase` is the ONE sentence every surface prints for verdict
coverage. One producer for the NUMBER is not enough on its own: five surfaces
each wording it themselves is how three of them came to describe the lifecycle
instead. Plain text, no markup, because both the HTML report and the chat reply
render it.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import explore_stop

DONE_VERDICTS: tuple[str, ...] = ("pass", "fail", "blocked", "unverified")


def _has_verdict(case: object) -> bool:
    """Whether ONE case record has reached a verdict."""
    body = case if isinstance(case, dict) else {}
    return str(body.get("verdict") or "").strip().lower() in DONE_VERDICTS


# How many records were ever MEANT to earn a verdict; see :func:`verdict_coverage`.
def _expected_verdicts(body: dict, lane: str, total: int) -> int:
    """How many records were ever MEANT to earn a verdict."""
    if lane == "explore":
        # `display_stop`, not `stop_reason`: this is a RENDER path (the page and
        # the status reply), and a coverage count that raised on a junk manifest
        # would take the whole page with it.
        return 1 if explore_stop.display_stop(body) else 0
    try:
        planned = int(body.get("total") or 0)
    except (TypeError, ValueError, OverflowError):
        planned = 0
    return max(planned, total)


def _status_count(items: list, status: str) -> int:
    """How many case records ended with *status* and no verdict."""
    return sum(
        1
        for c in items
        if isinstance(c, dict)
        and str(c.get("status") or "") == status
        and not _has_verdict(c)
    )


def _unfinished_tail(body: dict) -> str:
    """``"; 9 failed, 1 interrupted"`` for the turns that ended without a verdict."""
    parts = [
        f"{int(body.get(key) or 0)} {key}"
        for key in ("failed", "interrupted")
        if int(body.get(key) or 0)
    ]
    return ("; " + ", ".join(parts)) if parts else ""


def verdict_coverage(cases: Any, manifest: object = None) -> dict:
    """``{"lane", "total", "with_verdict", "expected", "complete"}``; never raises."""
    body = manifest if isinstance(manifest, dict) else {}
    items = [case for case in list(cases or [])]
    total = len(items)
    with_verdict = sum(1 for case in items if _has_verdict(case))
    lane = str(body.get("lane") or "suite")
    expected = _expected_verdicts(body, lane, total)
    return {
        "lane": lane,
        "total": total,
        "failed": _status_count(items, "failed"),
        "interrupted": _status_count(items, "abandoned"),
        "with_verdict": with_verdict,
        "expected": int(expected),
        "complete": bool(expected) and with_verdict >= expected,
    }


def _explore_phrase(body: dict, with_verdict: int, total: int) -> str:
    if not body.get("expected"):
        return (
            "an exploratory run earns its verdict when it stops, and this "
            "one has not stopped yet"
        )
    if not with_verdict:
        return "the run stopped without recording a verdict on any turn"
    return (
        "the run recorded a verdict on "
        + str(with_verdict)
        + " of its "
        + str(total)
        + " turn"
        + ("" if total == 1 else "s")
        + _unfinished_tail(body)
    )


def coverage_phrase(coverage: object) -> str:
    """The ONE sentence every surface prints for verdict coverage."""
    body = coverage if isinstance(coverage, dict) else {}
    with_verdict = int(body.get("with_verdict") or 0)
    total = int(body.get("total") or 0)
    if str(body.get("lane") or "") == "explore":
        return _explore_phrase(body, with_verdict, total)
    return (
        str(with_verdict)
        + " of "
        + str(max(total, int(body.get("expected") or 0)))
        + " cases reached a verdict"
        + _unfinished_tail(body)
    )

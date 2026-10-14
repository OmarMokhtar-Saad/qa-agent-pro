"""The ``qa_mobile_status`` block: where a run is, from disk only.

Thirteenth layer of the ``render_*`` split of ``render.py``.

``status_block`` -- what ``qa_mobile_status`` prints: where the run is, from
disk only.

*coverage_line* is the ONE verdict-coverage sentence, produced by
``run_store.coverage_phrase`` and handed in by the composition root -- the same
string ``summary_block`` prints, so the two blocks of one reply cannot disagree
about how far the run got. This module still imports nothing.
"""

from __future__ import annotations

from config.settings_guard import never_raise
from tools.mobile.render_field import _count, _field
from tools.mobile.render_status_lines import (
    _status_abandoned_lines,
    _status_head_lines,
    _status_stopped_lines,
)


#: The counter line below is SUITE vocabulary: it describes a PLAN, and an
#: exploratory run has none -- its turns are not planned cases. Printing it here
#: is how one reply said "0 done, 3 remaining of 3" three lines above a list of
#: three finished turns: a verdict-derived count beside a status-derived one.
#: This lane gets the counter it actually has, plus the one coverage sentence.
def _explore_counter(explore: dict, coverage_line: str) -> list[str]:
    lines = [
        "- turns: "
        + str(_count(explore.get("turn")))
        + " replayed of a "
        + str(_count(explore.get("turns_budget")))
        + "-turn budget"
    ]
    if coverage_line:
        lines.append("- " + _field(coverage_line))
    return lines


def _cases_counter(body: dict, total: int) -> list[str]:
    return [
        "- cases: "
        + str(int(body.get("done") or 0))
        + " done, "
        + str(len(body.get("failed") or []))
        + " failed, "
        + str(max(0, total - int(body.get("done") or 0)))
        + " remaining of "
        + str(total)
    ]


def _status_counter_lines(body: dict, coverage_line: str) -> list[str]:
    """The progress counter row(s): turns for an explore run, cases otherwise."""
    explore = body.get("explore")
    explore = explore if isinstance(explore, dict) else {}
    total = _count(body.get("total"))
    if str(body.get("lane") or "") == "explore":
        return _explore_counter(explore, coverage_line)
    return _cases_counter(body, total) if total else []


def _status_explore_line(body: dict) -> list[str]:
    """The ``exploring:`` row for a run with an explore record, or ``[]``."""
    explore = body.get("explore")
    if not (isinstance(explore, dict) and explore):
        return []
    return [
        "- exploring: turn "
        + str(int(explore.get("turn") or 0))
        + " of "
        + str(int(explore.get("turns_budget") or 0))
        + (
            " — stopped: " + _field(body.get("explore_stop"))
            if body.get("explore_stop")
            else ""
        )
    ]


def status_block(resolved: object, coverage_line: str = "") -> str:
    """What ``qa_mobile_status`` prints: where the run is, from disk only."""
    with never_raise():
        body = resolved if isinstance(resolved, dict) else {}
        lines = _status_head_lines(body)
        lines[2:2] = _status_stopped_lines(body)
        lines.extend(_status_counter_lines(body, coverage_line))
        holder = str(body.get("holder") or "")
        if holder:
            lines.append("- lease: held by session `" + holder[:40] + "`")
        lines += _status_abandoned_lines(body)
        lines += _status_explore_line(body)
        return "\n".join(lines)
    return "## Mobile run\n\n- state: **unknown**"

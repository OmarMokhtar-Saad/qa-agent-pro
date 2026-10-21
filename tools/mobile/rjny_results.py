"""The header's result lines and the list of issues."""

from __future__ import annotations

from tools.mobile.report_text import esc
from tools.mobile.report_verdict import _goal_result, _unify_verdict
from tools.mobile.report_widgets import _segbar, _tile_order
from tools.mobile.rjny_args import _ResultsIn
from tools.mobile.rjny_words import RESULTS, SUMMARY_ID, _count, _result_pill
from tools.mobile.run_mask_cue import mask_cues
from tools.mobile.run_mask_id import mask_id


def _issue_item(f: dict, kinds: dict) -> str:
    """One failed, blocked or crashed case as a list item, or ``""`` for any other case."""
    word = _unify_verdict(f["verdict"])
    crash = (f.get("crash") or {}).get("kind")
    if word not in ("fail", "blocked") and not crash:
        return ""
    anchor = ("turn-" if kinds.get(f["tc_id"]) == "turn" else "case-") + f["slug"]
    return (
        '<li class="issue">'
        + _result_pill(word if word in ("fail", "blocked") else "fail")
        + '<a href="#'
        + anchor
        + '">'
        + esc(f["tc_id"], 40)
        + '</a><span dir="auto">'
        + esc(f.get("finding") or f["reason"] or f["title"], 300)
        + (("; the app crashed (" + esc(crash, 40) + ")") if crash else "")
        + "</span></li>"
    )


def _issues_html(facts: list, kinds: dict) -> str:
    """Every case that failed, was blocked or crashed, or ``""``."""
    items = [i for i in (_issue_item(f, kinds) for f in facts) if i]
    return ('<ul class="issues">' + "".join(items) + "</ul>") if items else ""


def _stumbles(cases: list) -> str:
    """``"4 of 12 turns failed on the way"``, or ``""`` when no turn ended failed."""
    rows = [c for c in cases if isinstance(c, dict)]
    failed = sum(1 for c in rows if str(c.get("status")) == "failed")
    return (
        " (%d of %s failed on the way)" % (failed, _count(len(rows), "turn"))
        if failed
        else ""
    )


def _goal_line(group: dict) -> str:
    """One goal's result line; a Pass names the turns that failed on the way."""
    result = _goal_result(group["cases"])
    note = _stumbles(group["cases"]) if result == "pass" else ""
    return (
        '<li class="result">'
        + _result_pill(result)
        + '<span dir="auto">'
        + esc(mask_id(mask_cues(group["goal"])), 400)
        + "</span>"
        + ('<span class="rcount">' + esc(note, 80) + "</span>" if note else "")
        + "</li>"
    )


def _goal_lines(groups: list) -> list:
    """One result line per goal."""
    return [_goal_line(group) for group in groups]


def _scripted_line(scripted: list) -> str:
    """The one result line that counts the scripted cases by verdict word."""
    words: dict = {}
    for f in scripted:
        word = _unify_verdict(f["verdict"])
        words[word] = words.get(word, 0) + 1
    return (
        '<li class="result"><span class="rcount">'
        + esc(_count(len(scripted), "scripted case"), 40)
        + "</span><span>"
        + esc(
            " · ".join(str(words[w]) + " " + w for w in RESULTS if words.get(w)),
            120,
        )
        + "</span></li>"
    )


def _result_lines(data: _ResultsIn) -> list:
    """The header's result lines: the goals, the scripted count, or the empty note."""
    lines = _goal_lines(data.groups)
    if data.scripted:
        lines.append(_scripted_line(data.scripted))
    if not lines:
        lines.append(
            '<li class="result"><span>No case has been checkpointed yet.</span></li>'
        )
    return lines


def _results_html(data: _ResultsIn) -> str:
    """The header's result lines, inside the totals the selfcheck compares."""
    attrs = "".join(
        " data-" + name.replace("_", "-") + '="' + str(int(count)) + '"'
        for name, count in _tile_order(data.tally)
    )
    return (
        '<div id="'
        + SUMMARY_ID
        + '" data-total="'
        + str(int(data.total))
        + '"'
        + attrs
        + '><ul class="results">'
        + "".join(_result_lines(data))
        + "</ul>"
        + (_segbar(data.tally) if data.scripted else "")
        + "</div>"
    )

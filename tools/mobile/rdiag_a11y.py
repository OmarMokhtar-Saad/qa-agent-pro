"""Diagnostics accessibility section: what the run's own screens say about whether the app is usable."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tools.mobile.rdiag_a11yrows import _a11y_screen_row
from tools.mobile.report_widgets import _Head, _sechead


def _a11y_counts_clause(counts: Counter) -> str:
    total = counts["total"]
    audited = counts["audited"]
    return (
        str(total)
        + " finding"
        + ("" if total == 1 else "s")
        + " over "
        + str(audited)
        + " audited screen"
        + ("" if audited == 1 else "s")
        + ". These are usability defects, not functional ones: an accessibility "
        "finding NEVER changes a case's verdict, and no case here passed or "
        "failed because of one."
    )


def _a11y_gaps_clause(counts: Counter) -> str:
    return (
        _a11y_unauditable_clause(counts["unauditable"])
        + _a11y_partial_clause(counts["partial"])
        + _a11y_unaudited_clause(counts["unaudited"])
    )


def _a11y_unauditable_clause(unauditable: int) -> str:
    return (
        " "
        + str(unauditable)
        + " screen(s) exposed no accessibility nodes at all, so they were not "
        "audited -- an empty list there means nothing was looked at, not that "
        "nothing is wrong."
        if unauditable
        else ""
    )


def _a11y_partial_clause(partial: int) -> str:
    return (
        " " + str(partial) + " audited screen(s) were PARTIAL: elements were "
        "dropped before the audit ran, so those rows describe part of a screen."
        if partial
        else ""
    )


def _a11y_unaudited_clause(unaudited: int) -> str:
    return (
        " " + str(unaudited) + " screen(s) carry no audit at all." if unaudited else ""
    )


def _a11y_assumed_clause(assumed: int) -> str:
    return (
        " On "
        + str(assumed)
        + " screen(s) the device did not report its display density, so any "
        "touch-target finding there rests on an ASSUMED density and can be a "
        "false alarm on low-density hardware -- each such finding says so."
        if assumed
        else ""
    )


def _a11y_lede(counts: Counter) -> str:
    return (
        _a11y_counts_clause(counts)
        + _a11y_gaps_clause(counts)
        + _a11y_assumed_clause(counts["assumed"])
        + " Colour contrast, jank and start-up time are NOT checked here."
    )


def _a11y_table(rows: list) -> str:
    return (
        '<div class="tablewrap"><table class="cov"><thead><tr>'
        '<th scope="col" class="num">Screen</th>'
        '<th scope="col">What</th>'
        '<th scope="col">Detail</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


# What the run's own screens say about whether the app is USABLE.
#
# The findings come from ``screen_audit``, which ``perception.prune`` runs over
# every screen this lane ever pruned -- one producer, and this section reads the
# stored answer rather than deriving a second one.
#
# THREE outcomes that must never render alike, which is the whole point of the
# section: a screen with findings, a screen that was audited and had none, and a
# screen with no accessibility nodes AT ALL (a canvas app), where an empty list
# means nothing was looked at. A screen stored by a build older than the audit
# is a fourth: it carries no result, and this says so rather than counting it
# clean.
#
# Every interpolated value goes through :func:`esc` -- the labels are device
# text -- and only class names ``report_shell.html`` already styles are used.
# A finding NEVER touches a verdict: nothing here is read by
# ``_case_facts``, and the verdict producers do not import this module.
def _a11y_section(screens: object) -> str:
    library = screens if isinstance(screens, dict) else {}
    counts: Counter[Any] = Counter()
    rows = []
    for screen_id in sorted(str(key) for key in library.keys()):
        screen = library.get(screen_id)
        if not isinstance(screen, dict):
            continue
        rows.append(_a11y_screen_row(screen_id, screen.get("accessibility"), counts))
    if not rows:
        return ""
    return _sechead(
        "a11y",
        _Head(
            "Accessibility", "what the screens themselves say", _a11y_lede(counts), 3
        ),
        _a11y_table(rows),
    )

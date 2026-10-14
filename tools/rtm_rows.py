"""Rows for the 'Requirements Traceability' XLSX sheet."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools import rtm_consts as _k
from tools.rtm_cells import (
    _coverage_suppressed_row,
    _criterion_rows,
    _provenance_row,
    _suite_has_no_links,
    _untraced_row,
)
from tools.rtm_tracemap import _trace_map

logger = logging.getLogger("tools.rtm")


class _Sheet(NamedTuple):
    acs: list
    cases: list
    ac_to_tcs: dict
    orphans: list
    covered: int
    no_links: bool


def _coverage_row(s: _Sheet, kind: str) -> list:
    """The Coverage row: criteria covered, percentage, and cases traced."""
    covered, total, case_count = s.covered, len(s.acs), len(s.cases)
    traced = sum(len(tcs) for tcs in s.ac_to_tcs.values())
    pct = int(covered / total * 100) if total else 0
    return [
        "Coverage",
        f"{covered} of {total} {kind} covered ({pct}%) -- "
        f"{traced} of {case_count} case(s) trace to one.",
        "",
        "",
        "",
    ]


def _sheet(acs: list, cases: list) -> _Sheet:
    """The trace-map figures every row of the sheet is derived from."""
    ac_to_tcs, orphan_tc_ids = _trace_map(acs, cases)
    covered = sum(1 for tcs in ac_to_tcs.values() if tcs)
    no_links = _suite_has_no_links(cases, covered, orphan_tc_ids)
    return _Sheet(acs, cases, ac_to_tcs, orphan_tc_ids, covered, no_links)


def _head_rows(s: _Sheet) -> list:
    """Header, one row per criterion, and the "(untraced)" row when needed."""
    status_uncovered = _k._NOT_REPORTED if s.no_links else "ORPHAN"
    rows: list = [["AC ID", "Acceptance Criterion", "Cases", "Linked TCs", "Status"]]
    rows += _criterion_rows(s.acs, s.ac_to_tcs, status_uncovered)
    if s.orphans:
        rows.append(_untraced_row(s.orphans, s.no_links))
    return rows


def _foot_rows(s: _Sheet, derived: bool) -> list:
    """Blank spacer, the Coverage row, and the Provenance row when derived."""
    kind = "MODEL-DERIVED acceptance criteria" if derived else "acceptance criteria"
    rows: list = [["", "", "", "", ""]]
    if s.no_links:
        rows.append(_coverage_suppressed_row(len(s.cases), len(s.acs), kind))
    else:
        rows.append(_coverage_row(s, kind))
    if derived:
        rows.append(_provenance_row())
    return rows


# Rows (header first) for the 'Requirements Traceability' XLSX sheet.
#
# F06 (2026-08-19). The finalize reply's headline -- "7/7 acceptance criteria
# traced, all covered" -- was unverifiable by the person who RECEIVES the
# workbook. These rows are the SAME ``_trace_map`` computation the reply
# prints, shaped for a sheet, so the file and the claim cannot drift apart.
# The per-AC CASE COUNT is a column of its own on purpose: a total hides a
# suite of 25 cases on one criterion against 3 on another.
#
# *derived* means the criteria were SYNTHESIZED because the source carried none
# (``rtm_oneline`` says the same on the reply), so the caveat travels with the
# file. Pure -- cell sanitisation happens in tools/xlsx_generator. Returns []
# when there are no criteria, so no sheet is written. Never raises.
def rtm_rows(acs: list, test_cases: list, *, derived: bool = False) -> list:
    """Rows (header first) for the 'Requirements Traceability' XLSX sheet."""
    try:
        if not acs:
            return []
        s = _sheet(acs, list(test_cases or []))
        return _head_rows(s) + _foot_rows(s, derived)
    except Exception:
        logger.exception("rtm_rows failed -- returning []")
        return []

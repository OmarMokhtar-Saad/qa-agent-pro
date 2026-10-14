"""Cell builders for the Requirements Traceability sheet rows."""

from __future__ import annotations

import logging

from tools import rtm_consts as _k

logger = logging.getLogger("tools.rtm")


def _linked_cell(tc_ids: list) -> str:
    """A capped, comma-joined list of linked tc_ids, with the shortfall NAMED."""
    shown = ", ".join(str(t) for t in tc_ids[: _k._RTM_LINKED_CAP])
    extra = len(tc_ids) - _k._RTM_LINKED_CAP
    return f"{shown}, ... (+{extra} more)" if extra > 0 else shown


def _suite_has_no_links(cases: list, covered: int, orphan_tc_ids: list) -> bool:
    """True when NOT ONE case carries a usable `requirement_id`.

    D1 (2026-08-21). Two DIFFERENT outcomes were printed identically.

    2026-08-16, TICKET-5645: 40 of 64 cases linked, 24 did not, 3 criteria
    were never referenced. Those 3 are ORPHAN and "x of y covered (n%)" is
    a true statement about a suite that really does miss them.

    2026-08-21, TICKET-5646: the host returned `requirement_id: null` on all
    96 cases, and this sheet said "0 of 4 acceptance criteria covered (0%)"
    with four ORPHAN rows and no caveat. A tester reads that as a coverage
    FAILURE by the suite. It is not -- the cases plainly exercise the
    criteria; only the LINK FIELD is absent.

    So in that shape the sheet reports the data as ABSENT rather than the
    coverage as zero. `bool(cases)` matters: an EMPTY suite is not evidence of
    a broken generator, and it must keep today's wording.
    """
    return bool(cases) and covered == 0 and len(orphan_tc_ids) == len(cases)


def _criterion_rows(acs: list, ac_to_tcs: dict, status_uncovered: str) -> list:
    """One sheet row per criterion: id, description, case count, links, status."""
    rows: list = []
    for ac in acs:
        linked = ac_to_tcs.get(ac.ac_id) or []
        rows.append(
            [
                ac.ac_id,
                str(getattr(ac, "description", "") or "")[: _k._RTM_DESC_CAP],
                str(len(linked)),
                _linked_cell(linked),
                "Covered" if linked else status_uncovered,
            ]
        )
    return rows


def _untraced_desc(no_links: bool) -> str:
    """The "(untraced)" row description, worded for the shape of the suite."""
    return (
        "EVERY case in this suite is listed here: not one carries a "
        "usable `requirement_id`, so this sheet cannot say which "
        "requirement any of them tests. They are UNTRACED, which is not "
        "the same as untested."
        if no_links
        else "Cases carrying no `requirement_id`. They test something, but "
        "this sheet cannot say which requirement -- they are exactly "
        "the gap between the two case numbers on the coverage line "
        "below, and they raise no criterion's count."
    )


# F04 will make an untraced case legitimate; this row already renders one
# honestly rather than as an absence a reader has to notice.
#
# D1: the regular wording points at "the two case numbers on the coverage
# line below". In the no-links shape the coverage line below carries NO pair
# of case numbers, so the pointer would point at nothing. Say what is true in
# that shape instead of leaving a dangling cross-reference in the deliverable.
def _untraced_row(orphan_tc_ids: list, no_links: bool) -> list:
    """The "(untraced)" row listing cases that carry no usable requirement_id."""
    return [
        "(untraced)",
        _untraced_desc(no_links),
        str(len(orphan_tc_ids)),
        _linked_cell(orphan_tc_ids),
        "NOT TRACED",
    ]


def _coverage_suppressed_row(case_count: int, total: int, kind: str) -> list:
    """The Coverage row of the no-links shape.

    The percentage is SUPPRESSED, not rendered as 0%: 0% is a claim about the
    suite, and the only thing actually known is that the generator returned no
    links. The criteria rows are KEPT -- the reader still needs to see what was
    meant to be covered.
    """
    return [
        "Coverage",
        f"NOT REPORTED -- none of the {case_count} case(s) carries a "
        f"usable `requirement_id`, so coverage of the {total} {kind} "
        "cannot be measured from this suite. The percentage is "
        "SUPPRESSED rather than reported as 0%: these cases are "
        "untraced, NOT untested. Re-check the `requirement_id` on "
        "each case against the criteria above.",
        "",
        "",
        "",
    ]


def _provenance_row() -> list:
    """The caveat row that travels with a sheet of synthesized criteria."""
    return [
        "Provenance",
        "These criteria were SYNTHESIZED because the source carried "
        "none, so this table measures self-consistency, NOT coverage "
        "of stated requirements.",
        "",
        "",
        "",
    ]

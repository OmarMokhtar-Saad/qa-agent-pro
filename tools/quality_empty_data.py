"""Part of the quality-check split (see tools/quality_checks.py): empty data."""

from __future__ import annotations

import logging

from tools.models import TestCase

logger = logging.getLogger("tools.quality_checks")

# Suite-level shortfall thresholds. A single case with no preconditions and an
# empty test_data plan is normal (a pure navigation check needs neither), but a
# whole suite of them means the generator put every value in prose inside the
# step text, leaving the exported Preconditions and Test Data columns blank.
# Module constants, deliberately NOT settings flags (CLAUDE.md flag policy):
# this is an advisory warning with no per-install right answer to tune.


EMPTY_DATA_WARN_RATIO = 0.8

# EMPTY_DATA_MIN_CASES is ALSO the suite-size floor for data_notes_section's
# missing-plan disclosure (F01, 2026-08-16). Below it a fixture is too small for
# "N of M cases declare a data plan" to mean anything, and reusing this constant
# rather than inventing a second one keeps every 1-4 case unit fixture
# byte-identical. A real run is always >= 8 cases (8 categories), so the floor
# never suppresses the disclosure a tester would actually see.
EMPTY_DATA_MIN_CASES = 5


def find_empty_data_cases(cases: list[TestCase]) -> list[str]:
    """Return the tc_id of every case carrying BOTH no preconditions and an empty
    case-level test_data plan. Never raises."""
    try:
        return [
            tc.tc_id
            for tc in cases
            if not (getattr(tc, "preconditions", None) or "").strip()
            and not (getattr(tc, "test_data", None) or [])
        ]
    except Exception:
        logger.exception("find_empty_data_cases failed — returning empty list")
        return []


# A SPLIT from find_empty_data_cases above, not a redefinition: that function's
# BOTH-empty meaning is pinned by test and still supplies the detail count in
# quality_warning_section. The conjunction was the bug -- live run 2026-08-21,
# suite 817a09c8: 58 of 64 cases shipped an empty test_data plan (90.6%, past
# EMPTY_DATA_WARN_RATIO) while all carried good preconditions, so the BOTH-empty
# count was 0 and the advisory could not fire (blank Test Data on 58 rows).
# BOTH-empty is a strict subset of this: no warning that fired stops firing.
def find_empty_test_data_cases(cases: list[TestCase]) -> list[str]:
    """Return the tc_id of every case whose test_data plan is empty. Never raises."""
    try:
        return [tc.tc_id for tc in cases if not (getattr(tc, "test_data", None) or [])]
    except Exception:
        logger.exception("find_empty_test_data_cases failed — returning empty list")
        return []

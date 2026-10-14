"""Suite-level coverage-class detector.

Every other detector in this tree is PER-CASE ("is this oracle falsifiable?",
"is this first step findable?"). This one asks a SUITE-level question -- "does
any case cover class X at all?" -- and absence checks fail in the direction that
hurts: a case that merely MENTIONS push and failure scores as covered while
asserting only the success path.

So a class is only ``qualifying`` when ONE case satisfies BOTH a subject matcher
and a separate adversity matcher, and the adversity must appear in a step action
or an expected result -- NEVER in the title alone, because titles are where
aspirational wording lives. Both counts are reported: "3 push cases found, 0
qualify" is the useful tester-facing sentence AND the proof the matcher is not
inert.

Calibrated against suite 1ed83399b4b84831b79ead7936235989 (96 cases, hand-read),
where the ground truth is: classes 1-7 absent, 8 and 9 present at exactly one
case each. tests/test_coverage_classes.py pins that ground truth plus a positive
control per class, because a detector that matches NOTHING would also reproduce
the "all nine missing" headline.

A class the suite never MENTIONS is not reported at all: nothing here knows
what feature the suite covers, so an unmentioned class is as likely to be out
of scope as missing. See the scope gate in ``tools.ccl_detect.missing_finding``
for what that costs on the calibration suite.

ADVISORY only, like every detector beside it: it never rejects a case, never
triggers a regeneration, and so cannot inflate step counts.
"""

from __future__ import annotations

import logging

from tools.ccl_detect import _case_texts, missing_finding, prepare_cases
from tools.ccl_table_a import CLASSES_A
from tools.ccl_table_b import CLASSES_B
from tools.ccl_types import ClassFinding, CoverageClass

__all__ = [
    "COVERAGE_CLASSES",
    "ClassFinding",
    "CoverageClass",
    "_case_texts",
    "find_missing_coverage_classes",
]

logger = logging.getLogger(__name__)

# Table order is the report order (tests index by class position).
COVERAGE_CLASSES: list[CoverageClass] = [*CLASSES_A, *CLASSES_B]


def find_missing_coverage_classes(cases: list) -> list[ClassFinding]:
    """Every class whose qualifying-case count is below its floor.

    Returns them in COVERAGE_CLASSES order. Never raises -- on an internal
    failure it returns whatever it had, exactly like the detectors beside it.
    """
    out: list[ClassFinding] = []
    try:
        prepared = prepare_cases(cases)
        for spec in COVERAGE_CLASSES:
            finding = missing_finding(spec, prepared)
            if finding is not None:
                out.append(finding)
    except Exception:
        logger.exception(
            "find_missing_coverage_classes failed - returning what was found"
        )
    return out

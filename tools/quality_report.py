"""Part of the quality-check split (see tools/quality_checks.py): report."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.models import TestCase
from tools.quality_empty_data import (
    EMPTY_DATA_MIN_CASES,
    EMPTY_DATA_WARN_RATIO,
    find_empty_data_cases,
    find_empty_test_data_cases,
)
from tools.quality_findability import _unanchored_lines, find_unanchored_first_steps
from tools.quality_lines import (
    _empty_data_line,
    _placeholder_lines,
    _vague_expected_lines,
    _vague_lines,
)
from tools.quality_vague import (
    find_placeholder_data,
    find_vague_expected,
    find_vague_steps,
)

logger = logging.getLogger("tools.quality_checks")


def quality_ratio(cases: list[TestCase]) -> float:
    """Fraction of steps that are vague and/or have placeholder test data. Never raises."""
    try:
        total_steps = sum(len(tc.steps) for tc in cases)
        if total_steps == 0:
            return 0.0
        flagged_steps: set[tuple[str, int]] = set()
        for tc_id, step_number, _ in find_vague_steps(cases):
            flagged_steps.add((tc_id, step_number))
        for tc_id, step_number, _ in find_placeholder_data(cases):
            flagged_steps.add((tc_id, step_number))
        for tc_id, step_number, _ in find_vague_expected(cases):
            flagged_steps.add((tc_id, step_number))
        return len(flagged_steps) / total_steps
    except Exception:
        logger.exception("quality_ratio failed — returning 0.0")
        return 0.0


class _Findings(NamedTuple):
    """Everything quality_warning_section measures over one suite."""

    vague: list
    placeholder: list
    vague_expected: list
    empty_data: list
    empty_test_data: list
    unanchored: list
    total_cases: int

    # 2026-08-21: the RATIO is taken over the empty-test_data measure, which does
    # not also require empty preconditions. The old conjunction meant this
    # advisory could only fire on a suite that was ALSO missing its preconditions,
    # so the live 90.6%-empty run was invisible to it. BOTH-empty is a subset of
    # test_data-empty, so this is a strict superset of the old gate; empty_data
    # survives as the detail count. Suite-level: flagged only on a suite large
    # enough for the pattern to mean something.
    @property
    def empty_data_flagged(self) -> bool:
        return (
            self.total_cases >= EMPTY_DATA_MIN_CASES
            and len(self.empty_test_data) / self.total_cases >= EMPTY_DATA_WARN_RATIO
        )

    @property
    def any_flagged(self) -> bool:
        return bool(
            self.vague
            or self.placeholder
            or self.vague_expected
            or self.empty_data_flagged
            or self.unanchored
        )


def _findings(cases: list[TestCase]) -> _Findings:
    vague = find_vague_steps(cases)
    placeholder = find_placeholder_data(cases)
    vague_expected = find_vague_expected(cases)
    total_cases = len(list(cases))
    empty_data = find_empty_data_cases(cases)
    empty_test_data = find_empty_test_data_cases(cases)
    unanchored = find_unanchored_first_steps(cases)
    return _Findings(
        vague,
        placeholder,
        vague_expected,
        empty_data,
        empty_test_data,
        unanchored,
        total_cases,
    )


def _section_lines(found: _Findings, max_examples: int) -> list[str]:
    lines = ["\n\n## Data Quality Notes\n"]
    lines += _vague_lines(found.vague, max_examples)
    lines += _placeholder_lines(found.placeholder, max_examples)
    lines += _vague_expected_lines(found.vague_expected, max_examples)
    if found.empty_data_flagged:
        lines.append(
            _empty_data_line(found.empty_test_data, found.empty_data, found.total_cases)
        )
    lines += _unanchored_lines(found.unanchored, found.total_cases)
    return lines


def quality_warning_section(cases: list[TestCase], max_examples: int = 10) -> str:
    """Build a '## Data Quality Notes' block for vague/placeholder content; never raises.

    Returns '' when nothing is flagged or on any internal error.
    """
    try:
        found = _findings(cases)
        if not found.any_flagged:
            return ""
        return "\n".join(_section_lines(found, max_examples))
    except Exception:
        logger.exception("quality_warning_section failed — returning empty string")
        return ""

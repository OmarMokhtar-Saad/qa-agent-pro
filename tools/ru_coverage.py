"""Coverage heuristics: requirements no case mentions, and the advisory sections."""

from __future__ import annotations

import logging

from tools import ru_patterns as rp
from tools.models import TestCase
from tools.ru_consts import _COVERAGE_KINDS, _MAX_UNCOVERED_RATIO
from tools.ru_enum import _case_text
from tools.ru_model import RequirementUnit

logger = logging.getLogger("tools.requirement_units")

_ENUM_HEADER = (
    "\n\n## Undefined Option Values (advisory)",
    "",
    "These cases select an option the ticket does not define, so a tester "
    "cannot execute them as written:",
)


def _eligible(units: list[RequirementUnit]) -> list[RequirementUnit]:
    """Assignable units of a reportable kind that carry text."""
    return [
        u
        for u in units or []
        if u.is_assignable and u.kind in _COVERAGE_KINDS and u.text
    ]


def _terms(text: str) -> set[str]:
    """Content words (longer than 3 characters) of ``text``."""
    return {w for w in rp._WORD_RE.findall(text.lower()) if len(w) > 3}


def _case_terms(cases: list[TestCase]) -> set[str]:
    """Union of the content words of every case."""
    terms: set[str] = set()
    for tc in cases:
        terms |= _terms(_case_text(tc))
    return terms


def _uncovered(
    eligible: list[RequirementUnit], case_terms: set[str]
) -> list[RequirementUnit]:
    """Units whose content words share nothing with any case."""
    uncovered = []
    for unit in eligible:
        terms = _terms(unit.text)
        if terms and not (terms & case_terms):
            uncovered.append(unit)
    return uncovered


def _suppressed(
    uncovered: list[RequirementUnit], eligible: list[RequirementUnit]
) -> bool:
    """True (and logged) when too many units look uncovered to be a real gap."""
    if len(uncovered) <= len(eligible) * _MAX_UNCOVERED_RATIO:
        return False
    logger.info(
        "Uncovered-requirement heuristic flagged %d/%d units - suppressing "
        "as a vocabulary mismatch rather than a real gap",
        len(uncovered),
        len(eligible),
    )
    return True


# Target units of a reportable kind that NO case shares a content word with.
#
# Deliberately conservative -- a unit is reported only when the overlap with
# every case is EMPTY -- because this is advisory text a human reads, and a
# gap report that cries wolf is worse than no gap report. Never raises.
def find_unaddressed_requirements(
    units: list[RequirementUnit], cases: list[TestCase]
) -> list[RequirementUnit]:
    """Target units of a reportable kind that no case shares a content word with."""
    try:
        eligible = _eligible(units)
        if not eligible or not cases:
            return []
        case_terms = _case_terms(cases)
        if not case_terms:
            return []
        uncovered = _uncovered(eligible, case_terms)
        if _suppressed(uncovered, eligible):
            return []
        return uncovered
    except Exception:
        logger.exception("find_unaddressed_requirements failed - reporting none")
        return []


def coverage_warning_section(uncovered: list[RequirementUnit]) -> str:
    """Advisory markdown listing requirements no case appears to exercise."""
    try:
        if not uncovered:
            return ""
        lines = [
            "\n\n## Requirements With No Matching Case (advisory)",
            "",
            "Parsed from the ticket, but no generated case mentions them. Confirm "
            "they are covered or add cases:",
        ]
        for unit in uncovered[:10]:
            lines.append(f"- **{unit.unit_id}**: {unit.text[:160]}")
        if len(uncovered) > 10:
            lines.append(f"- ... and {len(uncovered) - 10} more")
        return "\n".join(lines)
    except Exception:
        logger.exception("coverage_warning_section failed - returning empty string")
        return ""


def _violation_lines(violations: list[tuple[str, str, str]]) -> list[str]:
    """Bullet lines for the first 20 violations plus an overflow note."""
    lines = [
        f'- **{tc_id}** - `{field}` = "{value}"'
        for tc_id, field, value in violations[:20]
    ]
    if len(violations) > 20:
        lines.append(f"- ... and {len(violations) - 20} more")
    return lines


def _defined_lines(defined: list[str]) -> list[str]:
    """The trailing 'Defined options' lines; empty when nothing is defined."""
    if not defined:
        return []
    return ["", "Defined options: " + ", ".join(f'"{d}"' for d in defined)]


def enum_warning_section(
    violations: list[tuple[str, str, str]], enum_values: dict[str, set[str]]
) -> str:
    """Advisory markdown listing enum-value violations. '' when there are none."""
    try:
        if not violations:
            return ""
        defined = sorted({v for vals in enum_values.values() for v in vals})
        lines = list(_ENUM_HEADER)
        lines.extend(_violation_lines(violations))
        lines.extend(_defined_lines(defined))
        return "\n".join(lines)
    except Exception:
        logger.exception("enum_warning_section failed - returning empty string")
        return ""

"""Grounding advisories for the finalize summary."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools.models import TestCase
from tools.requirement_units import (
    assignable_unit_ids,
    coverage_warning_section,
    enum_warning_section,
    enumerations,
    find_unaddressed_requirements,
    find_unknown_enum_values,
    free_text_tables,
    parse_requirement_units,
    source_ambiguity_issues,
)
from tools.suite_consistency import consistency_warning_section

if TYPE_CHECKING:
    from agents.scenario_args import GroundingSource

logger = logging.getLogger("agents.test_scenario_agent")


# The prompt's user message carries the target ticket AND, when the issue has a
# parent, a "## Parent Story (BACKGROUND ONLY ...)" block appended after it. The
# grounding checks must read the TARGET only: parsing the whole message would let
# a parent's own tables define what counts as an in-scope option, which is the
# provenance rule tools/requirement_units exists to enforce.
_PARENT_BLOCK_MARKER = "## Parent Story (BACKGROUND ONLY"


def target_source_text(user_msg: str) -> str:
    """The portion of the user message describing the TARGET ticket.

    Truncates at the parent-story background heading when present. Returns the
    message unchanged when there is no parent block. Never raises.
    """
    try:
        text = user_msg or ""
        index = text.find(_PARENT_BLOCK_MARKER)
        return text[:index] if index != -1 else text
    except Exception:
        logger.exception("target_source_text failed - using the whole message")
        return user_msg or ""


def _consistency_grounding_text(source: str, ac_texts: list[str] | None) -> str:
    """The grounding corpus for the invented-UI-string bullet.

    The target ticket's own description PLUS the acceptance criteria parsed FROM
    the source. On a Jira run the promised copy usually lives in an AC row ("the
    app shows ..."), not in the description, so grounding on the description
    alone would report the ticket's own wording as invented. Only SOURCE-parsed
    criteria are passed in by the caller -- model-derived ones would let the
    generator ground itself, which is the same provenance rule that keeps comment
    text out of ``source``.
    """
    return "\n".join([source, *(text for text in (ac_texts or []) if text)])


def _enum_coverage_section(cases: list[TestCase], source: str) -> str:
    """Unknown-option and unaddressed-requirement advisories ("" when clean)."""
    enum_values = enumerations(source)
    # Honour the ticket's own free-text escape: when a data-field table
    # declares a free-text row ("Other reason"), a value outside the
    # enumeration is legitimate; when it declares none, it is not.
    violations = find_unknown_enum_values(
        cases, enum_values, allow_free_text=bool(free_text_tables(source))
    )
    section = enum_warning_section(violations, enum_values)
    units = parse_requirement_units(source)
    if assignable_unit_ids(units):
        section += coverage_warning_section(find_unaddressed_requirements(units, cases))
    return section


def _source_defects_section(source: str) -> str:
    """Defects found in the SOURCE ticket itself, capped at ten ("" when none)."""
    issues = source_ambiguity_issues(source)
    if not issues:
        return ""
    lines = [
        "\n\n## Source Ticket Defects (advisory)",
        "",
        "Found in the ticket itself, not in the generated cases. These make "
        "requirements ambiguous to trace and should go back to whoever wrote "
        "the ticket:",
    ]
    lines.extend(f"- {issue}" for issue in issues[:10])
    if len(issues) > 10:
        lines.append(f"- ... and {len(issues) - 10} more")
    return "\n".join(lines)


# (consistency_section, grounding_section) for the finalize summary.
#
# * consistency_section -- unfalsifiable oracles, conditional actions,
#   contradictory state assumptions, and exact UI strings the source
#   never promises (tools/suite_consistency + tools/oracle_grounding).
# * grounding_section -- options a case selects that the ticket never defines,
#   requirements no case appears to exercise, and defects found in the SOURCE
#   ticket itself (duplicate rule/table ids, one English label used for two
#   different controls).
#
# ``source_text`` MUST be the target ticket's own description. It used to be
# re-derived by slicing ``user_msg`` at the parent-story heading, which fed the
# checks Jira COMMENT text -- letting a commenter define what counts as an
# in-scope option, or plant duplicate ids that produce ticket defects addressed
# to somebody else -- and simultaneously threw away every prompt block after
# that heading. ``user_msg`` remains only as a fallback for a prep record from
# an older build that carries no description.
#
# Both are ADVISORY and deterministic: no model call, no case is dropped or
# reordered, and each returns "" when it finds nothing -- so a clean suite's
# summary is byte-identical to before. This mirrors quality_warning_section,
# which likewise runs unconditionally rather than behind a flag, because an
# empty-when-clean advisory block has no behaviour to opt out of.
#
# Never raises: any failure yields two empty strings.
def grounding_sections(cases: list[TestCase], src: GroundingSource) -> tuple[str, str]:
    """(consistency_section, grounding_section) for the finalize summary."""
    try:
        source = src.source_text or target_source_text(src.user_msg)
        consistency = consistency_warning_section(
            cases, grounding_text=_consistency_grounding_text(source, src.ac_texts)
        )
        grounding = _enum_coverage_section(cases, source) + _source_defects_section(
            source
        )
        return consistency, grounding
    except Exception:
        logger.exception("grounding_sections failed - returning empty sections")
        return "", ""

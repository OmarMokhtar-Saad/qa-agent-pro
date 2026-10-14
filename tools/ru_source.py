"""Source-ticket analysis: ambiguity defects, enumerations and free-text tables."""

from __future__ import annotations

import logging

from tools.ru_cells import _label_kind
from tools.ru_flow import _split_rules
from tools.ru_model import DataFieldTable, _strip_placeholder_brackets
from tools.ru_tables import parse_data_field_tables
from tools.ru_uc import _uc_rows

logger = logging.getLogger("tools.requirement_units")


def _duplicate_rule_issues(description: str) -> list[str]:
    """One issue per business-rule id declared more than once."""
    declared: list[str] = []
    for label, value in _uc_rows(description):
        if _label_kind(label) == "business_rule":
            declared.extend(d for d, _ in _split_rules(value) if d)
    return [
        f"Business rule id {rule_id} is declared {declared.count(rule_id)} times "
        f"for different rules - renumber them so each rule is citable."
        for rule_id in sorted({d for d in declared if declared.count(d) > 1})
    ]


def _duplicate_table_issues(tables: list[DataFieldTable]) -> list[str]:
    """One issue per data-field table id used more than once."""
    table_ids = [t.table_id for t in tables]
    return [
        f"Data-field table id {table_id} is used {table_ids.count(table_id)} times "
        f"for different screens - give each table its own id."
        for table_id in sorted({t for t in table_ids if table_ids.count(t) > 1})
    ]


def _conflicting_label_issues(table: DataFieldTable) -> list[str]:
    """Issues for rows of one table sharing an English label but not the Arabic."""
    by_en: dict[str, list[str]] = {}
    for ar, en, _kind in table.rows:
        key = _strip_placeholder_brackets(en).lower()
        if key:
            by_en.setdefault(key, []).append(_strip_placeholder_brackets(ar))
    issues: list[str] = []
    for en_label, ar_labels in sorted(by_en.items()):
        if len(ar_labels) > 1 and len({a for a in ar_labels if a}) > 1:
            issues.append(
                f"{table.table_id}: the English label "
                f'"{en_label}" is used for {len(ar_labels)} different rows '
                f"({', '.join(a for a in ar_labels if a)}) - one of the "
                f"English strings is wrong."
            )
    return issues


# Defects in the SOURCE ticket, found deterministically -- no model call.
#
# Each returned string is a reviewer-facing issue suitable for the step-zero
# ambiguity result. These are ticket defects, not suite defects, and they are
# reported rather than resolved: guessing which of two identically-numbered
# rules a case traces to is the behaviour this whole batch removes.
#
# Detects:
#   * the same business-rule id declared twice (TICKET-5645 numbers two
#     different rules ``BR02``);
#   * the same data-field table id used twice (TICKET-5645 labels both the
#     confirmation dialog and the success screen ``DF02``);
#   * two rows of one table sharing an English label while their Arabic
#     differs (TICKET-5645's DF01 labels BOTH buttons "Keep the order", so the
#     primary button's English string is wrong).
#
# Never raises.
def source_ambiguity_issues(description: str) -> list[str]:
    """Defects in the SOURCE ticket, found deterministically; never raises."""
    issues: list[str] = []
    try:
        if not description or not description.strip():
            return []

        issues.extend(_duplicate_rule_issues(description))
        tables = parse_data_field_tables(description)
        issues.extend(_duplicate_table_issues(tables))
        for table in tables:
            issues.extend(_conflicting_label_issues(table))
    except Exception:
        logger.exception("source_ambiguity_issues failed - returning what was found")
    return issues


def enumerations(description: str) -> dict[str, set[str]]:
    """{table_id: {selectable English labels}} for every DF table that has any.

    This is the authoritative list a test case may select from, which is how
    :func:`find_unknown_enum_values` catches a case built around a reason label
    the ticket never defines.
    """
    out: dict[str, set[str]] = {}
    try:
        for table in parse_data_field_tables(description):
            values = table.enum_values()
            if values:
                out.setdefault(table.table_id, set()).update(values)
    except Exception:
        logger.exception("enumerations failed - returning what was parsed")
    return out


def free_text_tables(description: str) -> set[str]:
    """Ids of DF tables that also offer a free-text row (an 'Other' escape)."""
    try:
        return {
            t.table_id
            for t in parse_data_field_tables(description)
            if t.allows_free_text()
        }
    except Exception:
        logger.exception("free_text_tables failed - returning empty set")
        return set()

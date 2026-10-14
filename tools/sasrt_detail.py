"""Tester-facing prose for step_assertion findings (see tools/step_assertion.py)."""

from __future__ import annotations


def _by_category(findings: list) -> dict:
    """Group findings by category; a blank category reads "(uncategorised)"."""
    by_category: dict = {}
    for finding in findings:
        by_category.setdefault(finding.category or "(uncategorised)", []).append(
            finding
        )
    return by_category


def _category_line(category: str, rows: list) -> str:
    """One bullet: the category, its count and the first row as an example."""
    sample = rows[0]
    return (
        f"- {category}: {len(rows)} step(s), e.g. {sample.tc_id} "
        f'step {sample.step_number} -- "{sample.expected[:100]}"'
    )


def step_assertion_detail(findings: list) -> str:
    """Tester-facing prose for the Generation Notes sheet and the submit reply.

    Says what was measured and what it means for the suite, in the same register
    as the other Generation Notes entries: a shortfall stated plainly, with no
    instruction to the reader about what to do next.
    """
    if not findings:
        return ""
    by_category = _by_category(findings)
    lines = [
        f"{len(findings)} step(s) have an expected_result that asserts nothing "
        "the action did not already state -- typically a restatement of the "
        "action itself. Such a step passes against correct software AND against "
        "broken software, so executing it measures nothing.",
        "",
    ]
    lines.extend(_category_line(c, by_category[c]) for c in sorted(by_category))
    return "\n".join(lines)


def test_data_detail(findings: list) -> str:
    """Tester-facing prose for the test_data findings. Disclosure only."""
    if not findings:
        return ""
    sample = findings[0]
    return (
        f"{len(findings)} test_data field(s) use the field NAME as the example "
        f'value (e.g. {sample.tc_id}: "{sample.field}" -> '
        f'"{sample.example_value}"). That is a label, not a value a tester can '
        "enter, so the case does not say what to type. Nothing was refused over "
        "this -- it is a readability defect, not a correctness one."
    )

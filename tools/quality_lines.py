"""Part of the quality-check split (see tools/quality_checks.py): lines."""

from __future__ import annotations

from tools.quality_empty_data import EMPTY_DATA_WARN_RATIO


def _vague_lines(vague: list[tuple[str, int, str]], max_examples: int) -> list[str]:
    """The vague-phrasing bullet and its examples; empty when nothing is vague."""
    lines: list[str] = []
    if vague:
        lines.append(
            f"- {len(vague)} step(s) may use vague phrasing instead of a literal "
            "value — please review before executing:"
        )
        for tc_id, step_number, action in vague[:max_examples]:
            lines.append(f"  - {tc_id} step {step_number}: {action[:120]}")
    return lines


def _placeholder_lines(
    placeholder: list[tuple[str, int, str]], max_examples: int
) -> list[str]:
    """The placeholder-test-data bullet and its examples."""
    lines: list[str] = []
    if placeholder:
        lines.append(
            f"- {len(placeholder)} step(s) have placeholder test data instead of "
            "a concrete example value:"
        )
        for tc_id, step_number, test_data in placeholder[:max_examples]:
            lines.append(f"  - {tc_id} step {step_number}: {test_data}")
    return lines


def _vague_expected_lines(
    vague_expected: list[tuple[str, int, str]], max_examples: int
) -> list[str]:
    """The vague-expected-result bullet and its examples."""
    lines: list[str] = []
    if vague_expected:
        lines.append(
            f"- {len(vague_expected)} step(s) have a vague expected result "
            '(e.g. "appropriate error message") instead of the concrete outcome '
            "to verify:"
        )
        for tc_id, step_number, expected in vague_expected[:max_examples]:
            lines.append(f"  - {tc_id} step {step_number}: {expected[:120]}")
    return lines


# COUNTS ONLY, never an id list: this section is where the finalize reply's
# _SUMMARY_CAP truncation lands (live repro 2026-08-15). It leads with the RATIO
# judgement, not the raw missing count, so it does not restate
# data_notes_section's "N of M case(s) declare a data plan" disclosure; this is
# the advisory that the shape has taken over the suite. AND IT IS BUDGETED
# (reviewer F1, 2026-08-21, measured): a first draft ran 380 chars and pushed
# the reply to 4229 against mcp_handlers._SUMMARY_CAP = 4000, truncating the
# advisories that print after it. One line, ~134 chars at 96 cases; if it must
# grow, shorten something else -- do not raise the cap.
# F13 (2026-08-30): "case-level" says which field is empty (the export's Test
# Data column renders STEP-level test_data and is routinely full on a suite that
# trips this). A longer explanation measured +56 chars and pushed the reply past
# _SUMMARY_CAP; the place for the long version is the workbook.
# tests/test_finalize_reply_cap.py measures the tripped shape.
def _empty_data_line(
    empty_test_data: list[str], empty_data: list[str], total_cases: int
) -> str:
    """The one-line empty-test_data advisory."""
    pct = round(100 * len(empty_test_data) / total_cases)
    threshold_pct = round(100 * EMPTY_DATA_WARN_RATIO)
    return (
        f"- {pct}% of {total_cases} case(s) have no case-level "
        f"test_data (threshold {threshold_pct}%; {len(empty_data)} "
        "also lack preconditions) — add a test_data entry per field "
        "the case uses."
    )

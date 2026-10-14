"""Part of the quality-check split (see tools/quality_checks.py): data notes."""

from __future__ import annotations

import logging

from tools.models import TestCase
from tools.quality_data_gap import DATA_GAP_ROW_COST, _data_gap_line

logger = logging.getLogger("tools.quality_checks")

# Cap on the per-case enumeration in data_notes_section. One line per case is a
# flood on a real run — 96 cases meant 96 lines, which pushed the finalize reply
# past the 4000-char cap in handlers.shape_generation_result and
# truncated everything after this section (live repro 2026-08-15). Same failure
# shape as the checklist NOT-COVERED flood suppressed in tools/rtm.py. The full
# plan still ships in the Test Data column of the exported file. Module
# constant, deliberately NOT a settings flag (CLAUDE.md flag policy): a display
# cap has no per-install right answer to tune.


DATA_NOTES_MAX_CASES = 10


# F01 (live run 2026-08-16, suite 1ed83399): 19 of 96 cases shipped an empty
# ``test_data`` plan. That is CONTRACTUALLY LEGAL (the generator is told to leave
# the array empty when a case manipulates no data), so this is a DISCLOSURE, not
# a warning: enumerating only the 77 cases that HAD a plan left readers unable to
# tell "needs no data" from "plan missing". The disclosure names the affected ids
# (at most DATA_GAP_MAX_IDS, each clipped to DATA_GAP_MAX_ID_CHARS, then
# "(+N more)") only while they are a MINORITY (below EMPTY_DATA_WARN_RATIO);
# at or above the ratio the line is byte-identical to its F01 form, because the
# suite-level advisory owns that message and that shape has the least reply
# headroom (see the constants in quality_data_gap). The ids are paid for out of
# this section's own enumeration budget (see `_row_limit`), since the finalize
# reply is capped (handlers._SUMMARY_CAP = 4000);
# tests/test_finalize_reply_cap.py measures 0/96, 87/96 and 19/96.
# Fires when at least one case declares no plan AND the suite has at least
# EMPTY_DATA_MIN_CASES cases; a suite where NO case declares one is not silent.
def data_notes_section(cases: list[TestCase]) -> str:
    """Build the '## Test Data' note: one line per case plus a bounded gap line.

    Never raises.
    """
    try:
        rows, missing_ids, total = _data_note_rows(cases)
        gap = _data_gap_line(total, missing_ids)
        return _data_notes_text(rows, gap)
    except Exception:
        logger.exception("test_data_notes_section failed — returning empty string")
        return ""


def _data_note_rows(cases: list[TestCase]) -> tuple[list[str], list[str], int]:
    """The enumerated plan rows, the ids declaring no plan, and the case total."""
    rows: list[str] = []
    total = 0
    missing_ids: list[str] = []
    for tc in cases:
        total += 1
        if not getattr(tc, "test_data", None):
            missing_ids.append(str(getattr(tc, "tc_id", "") or "?"))
            continue
        fields = ", ".join(f"{it.field}={it.strategy}" for it in tc.test_data)
        rows.append(f"- {tc.tc_id}: {fields}")
    return rows, missing_ids, total


# DATA_GAP_ROW_COST fewer enumerated rows when the gap line renders, so the
# disclosure is paid for out of THIS section's budget instead of the shared
# _SUMMARY_CAP; the dropped rows roll into the overflow count and the full plan
# is in the exported file either way.
def _row_limit(rows: list[str], gap: str) -> int:
    """How many plan rows to enumerate, given the gap line."""
    limit = DATA_NOTES_MAX_CASES
    # THE OVERFLOW CONDITION IS LOAD-BEARING. Charging the cost when the
    # enumeration fits today CREATES an "... and N more" line that was
    # absent, which costs ~87 chars to save ~150 of rows and can end up NET
    # POSITIVE -- measured at +42 on the 87-of-96 fixture in
    # tests/test_finalize_reply_cap.py, i.e. 4025 against a 4000 cap. So the
    # rows are only ever taken from an enumeration that already overflows,
    # where each dropped row is a pure saving. Never let this become an
    # unconditional `if gap`.
    if gap and len(rows) > DATA_NOTES_MAX_CASES:
        limit = max(1, DATA_NOTES_MAX_CASES - DATA_GAP_ROW_COST)
    return limit


def _data_notes_text(rows: list[str], gap: str) -> str:
    """Assemble the '## Test Data' block from the plan rows and the gap line."""
    if not rows:
        # Nothing to enumerate: stay silent unless the gap line has something to say.
        return f"\n\n## Test Data\n{gap}".rstrip() if gap else ""
    header = (
        "\n\n## Test Data\n"
        f"{gap}"
        "Each case below declares what data it needs and how to source it "
        "(see the **Test Data** column in the exported file):\n"
    )
    shown = rows[: _row_limit(rows, gap)]
    overflow = len(rows) - len(shown)
    if overflow:
        shown.append(
            f"- … and {overflow} more case(s) — the full plan is in the "
            "Test Data column of the exported file."
        )
    return header + "\n".join(shown)

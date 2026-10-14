"""Part of the quality-check split (see tools/quality_checks.py): data gap."""

from __future__ import annotations

from tools.quality_empty_data import EMPTY_DATA_MIN_CASES, EMPTY_DATA_WARN_RATIO

# D1 (TICKET-5138, 2026-08-21): the gap line NAMES the affected cases now --
# but only while they are a MINORITY of the suite. F01 (2026-08-16) made the
# line counts-only for reply-budget reasons, and the count duly fired on the
# live run ("49 of 64 ... 15 declare none"); what it could not do is tell the
# tester to open TC-006 (catalog API load), TC-024 (HTTP 503) or TC-041 (BR01
# alphabetical order). A fifth prompt clause was rejected rather than tried:
# memory e02-null-result-2026-08-20 measured prose as a CONFIRMED WEAK LEVER
# on this generator, and this finding already recurred once (19 of 96, memory
# live-validation-2026-08-16) with all four existing clauses live.
#
# THE RATIO GATE IS LOAD-BEARING, AND MEASURED. Above EMPTY_DATA_WARN_RATIO
# the ids are suppressed and this line is byte-identical to its pre-TICKET-5138
# form, for two independent reasons: (1) the suite-level advisory in
# quality_warning_section already owns that message ("91% of 96 case(s) have
# no test data plan"), so 12 of 87 ids is noise on top of it; (2) that shape
# has the LEAST reply headroom of any -- a suite where almost nothing
# declares a plan has almost no enumeration left to shrink in payment. A
# first draft of this change named ids unconditionally and measured the
# 87-of-96 fixture in tests/test_finalize_reply_cap.py at 4025 chars against
# mcp_handlers._SUMMARY_CAP = 4000 -- over by 25, because dropping
# enumeration rows there CREATED an "... and N more" overflow line that had
# been absent. See `limit` below for the second half of that guard.
# Measured after the gate: 0/96 and 87/96 byte-identical, 19/96 -47 chars.
# Twelve ids covers the live 15-case run with a "(+3 more)" tail, and the
# per-id width cap bounds the whole line at ~190 chars even for a malformed
# tc_id. Module constants, deliberately NOT settings flags (CLAUDE.md flag
# policy): a display cap has no per-install right answer to tune.
DATA_GAP_MAX_IDS = 12

DATA_GAP_MAX_ID_CHARS = 12

DATA_GAP_ROW_COST = 4


# Bounded gap line: counts always, plus the affected ids while they are a
# MINORITY of the suite (see the constants above for why the ratio gate is not
# optional). Rendered AHEAD of the enumeration so a cut can never eat it.
def _data_gap_line(total: int, missing_ids: list[str]) -> str:
    """The bounded disclosure line for cases with no plan; '' when none applies."""
    missing = len(missing_ids)
    if not (missing and total >= EMPTY_DATA_MIN_CASES):
        return ""
    named = ""
    if missing < total * EMPTY_DATA_WARN_RATIO:
        shown_ids = [
            str(i)[:DATA_GAP_MAX_ID_CHARS] for i in missing_ids[:DATA_GAP_MAX_IDS]
        ]
        extra = missing - len(shown_ids)
        tail = f" (+{extra} more)" if extra > 0 else ""
        named = f": {', '.join(shown_ids)}{tail}"
    return (
        f"{total - missing} of {total} case(s) declare a data plan; "
        f"{missing} declare none (blank Test Data column){named}.\n"
    )

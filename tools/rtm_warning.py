"""Escalate a degenerate traceability outcome into a tester-facing warning."""

from __future__ import annotations

import logging

from tools.rtm_tracemap import _trace_map

logger = logging.getLogger("tools.rtm")

_DEGENERATE_HEAD = "\n\n> ⚠️  **Requirement traceability looks degenerate.** "

_DEGENERATE_TAIL = (
    " Traceability is unreliable for this suite: the RTM above cannot "
    "tell you which requirements are actually tested. Re-check the "
    "`requirement_id` on each case against the AC list."
)


# The lone-AC + orphan-majority signature: on 2026-08-03 (run f9094582) a
# DATE-valued custom field was parsed as the only "AC", 61/98 cases traced to
# nothing, and this advisory stayed silent behind `total <= 1` while the RTM
# read as covered.
def _lone_ac_warning(ac_to_tcs: dict, orphan_count: int, case_count: int) -> str:
    """The warning for ONE parsed AC with an orphan majority, else ""."""
    if orphan_count / case_count <= 0.5:
        return ""
    only_id = next(iter(ac_to_tcs), "AC-001")
    return (
        _DEGENERATE_HEAD
        + f"Only ONE acceptance criterion (`{only_id}`) was parsed from "
        f"the source, and {orphan_count} of {case_count} "
        "case(s) trace to nothing. A single AC with an orphan majority "
        "usually means the AC source field is misconfigured (for "
        "example `JIRA_AC_FIELD` pointing at a non-AC custom field), "
        "so the RTM cannot tell you which requirements are actually "
        "tested. Verify the AC field before trusting this suite's "
        "coverage numbers."
    )


def _degenerate_body(ac_to_tcs: dict, orphan_count: int, total: int) -> str:
    """The count sentence for a suite where at most ONE of several ACs is cited."""
    covered = sum(1 for tcs in ac_to_tcs.values() if tcs)
    if covered == 0:
        return (
            f"No test case traces to any of the {total} acceptance criteria "
            f"({orphan_count} case(s) carry no usable `requirement_id`)."
        )
    cited = next((ac_id for ac_id, tcs in ac_to_tcs.items() if tcs), "")
    traced = sum(len(tcs) for tcs in ac_to_tcs.values())
    return (
        f"{traced} case(s) trace to `{cited}` and {orphan_count} "
        f"trace to nothing, out of {total} acceptance criteria "
        f"({total - covered} never referenced)."
    )


# build_rtm_summary prints "Coverage: 1 of 7 ACs covered (14%)" and on the
# 2026-07-29 and 2026-07-30 runs nobody read it -- a percentage reads as a
# metric, not as a defect. This names it.
#
# Fires when more than one AC exists but at most ONE is cited, and when exactly
# ONE AC was parsed yet most cases trace to nothing (the misparsed-AC-source
# signature). ``covered <= 1``, not ``== 1``: zero is strictly WORSE.
#
# Counts are REAL, never "all N cases": orphans are counted apart. FLAG ONLY --
# nothing is dropped or rewritten. States an observation, not an accusation.
# Never raises.
def traceability_warning_section(acs: list, test_cases: list) -> str:
    """Escalate a DEGENERATE traceability outcome from a percentage to a finding."""
    try:
        if not acs or not test_cases:
            return ""
        ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
        total = len(acs)
        covered = sum(1 for tcs in ac_to_tcs.values() if tcs)
        if total == 1:
            return _lone_ac_warning(ac_to_tcs, len(orphan_tc_ids), len(test_cases))
        if total <= 1 or covered > 1:
            return ""
        body = _degenerate_body(ac_to_tcs, len(orphan_tc_ids), total)
        return _DEGENERATE_HEAD + body + _DEGENERATE_TAIL
    except Exception:
        logger.exception("traceability_warning_section failed -- returning empty")
        return ""

"""The oracle, conditional-action and conflict bullets of the advisory block."""

from __future__ import annotations

from collections import defaultdict

from tools import suite_consistency_limits as _lim


def _add_oracle_bullet(lines: list[str], order: list[tuple[str, int]], oracles) -> None:
    order.append(("soft oracles", _lim._ADV_P_UNJUDGEABLE))
    by_case: dict[str, list[int]] = defaultdict(list)
    for tc_id, step_number, _ in oracles:
        by_case[tc_id].append(step_number)
    lines.append(
        f"- **{len(by_case)} case(s) have an expected result that accepts more "
        "than one outcome**, so the step cannot fail and cannot detect a "
        "defect. Where the ticket genuinely does not specify the outcome, "
        "convert the case to an exploratory charter that records the actual "
        "behaviour instead of asserting one:"
    )
    for tc_id in sorted(by_case)[: _lim._MAX_REPORTED]:
        steps = ", ".join(str(n) for n in sorted(by_case[tc_id]))
        lines.append(f"  - {tc_id} (step {steps})")
    if len(by_case) > _lim._MAX_REPORTED:
        lines.append(f"  - ... and {len(by_case) - _lim._MAX_REPORTED} more")


# Its own bullet, not merged into the oracle list: a soft oracle
# means the tester runs the step and cannot fail it, a conditional
# action means the tester cannot tell whether to run it at all, and
# one count would misdescribe both. Same block, because a fourth
# heading costs reply budget for advice of the same class.
def _add_conditional_bullet(
    lines: list[str], order: list[tuple[str, int]], conditional
) -> None:
    order.append(("conditional actions", _lim._ADV_P_UNEXECUTABLE))
    by_action: dict[str, list[int]] = defaultdict(list)
    for tc_id, step_number, _ in conditional:
        by_action[tc_id].append(step_number)
    lines.append(
        f"- **{len(by_action)} case(s) have an action the tester "
        'cannot execute as written** — it is conditional ("if '
        'visible", "if available"), so whether the step is performed '
        "at all depends on what the build happens to show, and a Pass "
        "is indistinguishable from a skip. Name the entry point the "
        "case means, or split the conditional branch into its own case:"
    )
    for tc_id in sorted(by_action)[: _lim._MAX_REPORTED]:
        numbers = ", ".join(str(n) for n in sorted(by_action[tc_id]))
        lines.append(f"  - {tc_id} (step {numbers})")
    if len(by_action) > _lim._MAX_REPORTED:
        lines.append(f"  - ... and {len(by_action) - _lim._MAX_REPORTED} more")


def _add_conflict_bullet(
    lines: list[str], order: list[tuple[str, int]], conflicts
) -> None:
    order.append(("contradictory state assumptions", _lim._ADV_P_CROSS_CASE))
    lines.append(
        "- **Contradictory state assumptions** — the same seeded state is "
        "treated as both allowing and blocking the action. The ticket does "
        "not resolve this; confirm the rule before executing either side:"
    )
    for state, avail, block in conflicts[: _lim._MAX_REPORTED]:
        lines.append(
            f"  - `{state}`: allowed by {', '.join(avail)} / "
            f"blocked by {', '.join(block)}"
        )

"""Cross-case contradictions: one seeded state that both allows and blocks an action."""

from __future__ import annotations

import logging
from collections import defaultdict

from tools.models import TestCase
from tools.suite_consistency_polarity import _expected_polarity
from tools.suite_consistency_state_rx import _TERMINAL_STATE_TOKENS
from tools.suite_consistency_state_seed import _seeded_states

_Buckets = dict[str, list[tuple[str, frozenset[str]]]]


logger = logging.getLogger("tools.suite_consistency")


def _ids_sharing_subject(
    mine: list[tuple[str, frozenset[str]]],
    theirs: list[tuple[str, frozenset[str]]],
) -> list[str]:
    """Sorted tc_ids in *mine* whose subject overlaps any subject in *theirs*."""
    return sorted(
        {tc_id for tc_id, subj in mine if any(subj & other for _o, other in theirs)}
    )


def _bucket_case(tc: TestCase, available: _Buckets, blocked: _Buckets) -> None:
    resolved = _expected_polarity(tc)
    if resolved is None:
        return
    polarity, subject = resolved
    if not subject:
        return
    tc_id = getattr(tc, "tc_id", "") or ""
    for state in _seeded_states(tc):
        if any(token in state for token in _TERMINAL_STATE_TOKENS):
            continue
        bucket = available if polarity == "available" else blocked
        bucket[state].append((tc_id, frozenset(subject)))


def _collect_conflicts(
    cases: list[TestCase], out: list[tuple[str, list[str], list[str]]]
) -> None:
    available: _Buckets = defaultdict(list)
    blocked: _Buckets = defaultdict(list)
    for tc in cases or []:
        _bucket_case(tc, available, blocked)
    for state in sorted(set(available) & set(blocked)):
        # Only a shared subject makes this a contradiction rather than two
        # unrelated assertions that happen to seed the same status.
        yes = _ids_sharing_subject(available[state], blocked[state])
        no = _ids_sharing_subject(blocked[state], available[state])
        if yes and no:
            out.append((state, yes, no))


def _collect_safely(cases: list[TestCase]) -> list[tuple[str, list[str], list[str]]]:
    out: list[tuple[str, list[str], list[str]]] = []
    try:
        _collect_conflicts(cases, out)
    except Exception:
        logger.exception(
            "find_contradictory_state_assumptions failed - returning what was found"
        )
    return out


def find_contradictory_state_assumptions(
    cases: list[TestCase],
) -> list[tuple[str, list[str], list[str]]]:
    """(state_value, tc_ids expecting available, tc_ids expecting blocked).

    Reports each seeded state that one case treats as allowing the action while
    another treats it as blocking the action. Terminal states (``cancelled``,
    ``completed``) are excluded: there, "the action is gone" is what every case
    should expect, so a mixed reading is not a conflict.

    On the TICKET-5645 suite this reports ``processing`` -- seeded as the
    cancellable status by three cases and as the blocking status by a fourth,
    which the ticket never resolves. Never raises.
    """
    return _collect_safely(cases)

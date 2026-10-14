"""The advisory text-agreement label of a duplicate group."""

from __future__ import annotations

import difflib

from agents.host_dedup_consts import _DUP_WS_RE, _UNTRUSTED_ERRORS, logger
from agents.host_dedup_limits import _dup_keeper_key


def _dup_text(tc) -> str:
    """Normalised comparison text for the ADVISORY agreement label: title + FIRST
    step action, lower-cased with every non-alphanumeric run collapsed to one space.

    Deliberately mirrors ``agents.test_scenario_agent._semantic_payload``'s choice of
    fields, so the reported number describes the same content that helper
    compares. Never raises.
    """
    try:
        title = getattr(tc, "title", "") or ""
        steps = getattr(tc, "steps", None) or []
        action = (getattr(steps[0], "action", "") or "") if steps else ""
        return _DUP_WS_RE.sub(" ", f"{title} {action}".lower()).strip()[:600]
    except _UNTRUSTED_ERRORS:
        return ""


def _dup_text_ratio(a, b) -> float:
    """Server-measured textual agreement in [0, 1] between two cases (stdlib
    ``difflib`` only -- no optional dependency, no network, no LLM, no
    async, no I/O). Never raises.

    ADVISORY ONLY. It is REPORTED, never used to veto or to authorise a removal --
    see ``dup_agreements`` for the measurements that forbid gating on it.
    """
    try:
        ta, tb = _dup_text(a), _dup_text(b)
        if not ta or not tb:
            return 0.0
        return difflib.SequenceMatcher(None, ta, tb).ratio()
    except _UNTRUSTED_ERRORS:
        return 0.0


def _group_indices(cases: list, members: list) -> list:
    """Positions in ``cases`` of a group's members, first occurrence wins. Pure."""
    by_id: dict = {}
    for i, tc in enumerate(cases):
        by_id.setdefault(tc.tc_id, i)
    return [by_id[m] for m in members if m in by_id]


def _group_agreement(cases: list, members: list) -> float:
    """Lowest text agreement between a group's keeper and its other members."""
    idxs = _group_indices(cases, members)
    if len(idxs) < 2:
        return 0.0
    keep_idx = min(((i, cases[i]) for i in idxs), key=_dup_keeper_key)[0]
    ratios = [_dup_text_ratio(cases[i], cases[keep_idx]) for i in idxs if i != keep_idx]
    return min(ratios) if ratios else 0.0


# For each group, the LOWEST server-measured text agreement between its keeper
# and any other member -- an ADVISORY signal shown next to the group in the reply.
#
# WHY THIS IS NOT A GATE (measured, 2026-07-29, on this metric and on token
# Jaccard). The review asked for a lexical similarity FLOOR that a group must clear
# before a removal is honoured. It cannot be one: the metric does not separate the
# classes it would have to separate.
#
#     pair                                            difflib  jaccard
#     the MOTIVATING cross-category duplicate            0.29     0.25
#       ("Cannot cancel another user's order by
#        changing the order ID" vs "Attempt to cancel
#        an order belonging to a different account")
#     two UNRELATED same-domain cases                    0.28     0.05
#     two UNRELATED cases                                0.34     0.14
#     two near-identical boilerplate cases               0.97     0.82
#     two boundary siblings (must NOT be merged)         0.95     0.83
#
# A genuine duplicate scores 0.29 while an unrelated pair scores 0.28-0.34, and a
# pair that must NOT be merged scores 0.95. Any floor high enough to reject the
# hostile pairs also rejects the exact duplicate that motivates the feature, and
# any floor low enough to admit it admits everything. Aggregating over a whole
# review does not rescue it either: on a templated 64-case suite the medians were
# 0.97 genuine vs 0.57 hostile, but on hand-written text 0.29 vs 0.28 -- the scale
# is entirely corpus-dependent, which is exactly why this signal FLAGS instead
# of dropping.
#
# Shipping an uncalibrated number as a security bound on a DESTRUCTIVE path would
# be worse than shipping none: it would look like a guarantee. So the number is
# MEASURED and REPORTED (the tester gets the discriminating signal) while the
# actual bounds on removal are the two corpus-independent ones in
# ``screen_duplicate_groups``. Never raises.
def dup_agreements(cases: list, groups: list) -> list:
    """For each group, the LOWEST server-measured text agreement between its keeper and any other member (ADVISORY)."""
    out: list = []
    try:
        for members in groups or []:
            out.append(_group_agreement(cases or [], members or []))
    except _UNTRUSTED_ERRORS:
        logger.warning("dup_agreements failed -- omitting the labels", exc_info=True)
        return [0.0 for _ in (groups or [])]
    return out

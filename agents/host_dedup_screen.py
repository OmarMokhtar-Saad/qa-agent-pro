"""The deterministic safety screen gating every duplicate REMOVAL."""

from __future__ import annotations

from agents.host_dedup_agree import _group_indices
from agents.host_dedup_consts import (
    _DUP_MAX_APPLY_GROUP_SIZE,
    _MAX_DUP_NOTES,
    _UNSCREENED_NOTE,
    _UNTRUSTED_ERRORS,
    logger,
)
from agents.host_dedup_limits import _dup_keeper_key, _removal_ratio


def _oversize_refusal(n: int) -> str:
    """The note for a group refused for naming more cases than the apply bound."""
    return (
        f"a group naming {n} cases was NOT removed: more than "
        f"{_DUP_MAX_APPLY_GROUP_SIZE} cases in one duplicate cluster "
        "is not a duplicate, so it is reported for review instead."
    )


def _screen_group_sizes(cases: list, groups: list, refusals: list) -> list:
    """Groups as ``[keeper_id, *others]``; an oversized group is refused (noted in
    ``refusals``, never truncated)."""
    screened: list = []
    for members in groups:
        idxs = _group_indices(cases, members)
        if len(idxs) < 2:
            continue
        if len(idxs) > _DUP_MAX_APPLY_GROUP_SIZE:
            if len(refusals) < _MAX_DUP_NOTES:
                refusals.append(_oversize_refusal(len(idxs)))
            continue
        keep_idx = min(((i, cases[i]) for i in idxs), key=_dup_keeper_key)[0]
        screened.append(
            [cases[keep_idx].tc_id] + [cases[i].tc_id for i in idxs if i != keep_idx]
        )
    return screened


def _removal_refusal(removable: int, total: int, limit: int) -> str:
    """Log and word the whole-review refusal for the proportional cap."""
    logger.warning(
        "host duplicate review refused: %d of %d cases proposed for removal (bound %d)",
        removable,
        total,
        limit,
    )
    return (
        f"REFUSED: the submitted duplicate review would remove {removable} "
        f"of {total} submitted case(s), above the "
        f"{_removal_ratio():.0%} safety bound ({limit} case(s)). NOTHING was "
        "removed and NO group is treated as a duplicate. A review that large "
        "is handled as untrusted input, not as a judgement -- the groups are "
        "still listed below for you to act on yourself."
    )


# The DETERMINISTIC SAFETY SCREEN gating every REMOVAL. Returns
# ``(screened_groups, refusals)``. Called ONLY on the apply path.
#
# Both bounds are CORPUS-INDEPENDENT and need no calibration -- that is the whole
# point, because the field is attacker-influenced (the threat is not "an
# untrustworthy host model" but injected content inside the ``_GUARD``-wrapped
# Jira/comment text that host mode deliberately places in the host's context) and a
# tuned lexical threshold would be a guarantee in name only (see
# ``dup_agreements``).
#
# 1. **APPLY-PATH GROUP-SIZE BOUND** (``_DUP_MAX_APPLY_GROUP_SIZE``). A group
#    naming more than 4 cases is refused OUTRIGHT (never truncated -- partially
#    honouring a group nobody vouched for is worse). Rationale from the design, not
#    from a corpus: the fan-out has 8 categories and the project rule is one
#    BEHAVIOUR per test, so a genuine cross-category duplicate cluster is 2 cases,
#    occasionally 3. A 12-member group is not a duplicate cluster, it is a
#    partition primitive. This alone cuts the theoretical removal set from
#    50 x 11 = 550 ids to 50 x 3 = 150.
# 2. **PROPORTIONAL CAP** (``_removal_ratio()``, default 35%, ceiling 40%). If the
#    surviving groups would still remove more than that share of the SUBMITTED
#    cases, the WHOLE review is refused -- nothing removed, no group honoured --
#    and the refusal is reported verbatim. This is the bound that actually closes
#    the disjoint-partition attack: whatever the text says, a 64-case suite cannot
#    drop below 42 cases. ``max(1, ...)`` keeps a 2-case suite able to drop one
#    real duplicate.
#
# Why the SHAPE caps in ``_extract_duplicate_groups`` are not enough: they permit
# 50 x 12 = 550 removable ids, and their overlap rule only stops CHAINING, so 5
# DISJOINT groups of 12 would reduce a 64-case suite to 9. Never raises; on any
# failure NO group is honoured (the safe direction).
def screen_duplicate_groups(cases: list, groups: list) -> tuple[list, list]:
    """The DETERMINISTIC SAFETY SCREEN gating every REMOVAL. Returns
    ``(screened_groups, refusals)``. Called ONLY on the apply path. Never raises."""
    refusals: list = []
    if not cases or not groups:
        return [], refusals
    try:
        screened = _screen_group_sizes(cases, groups, refusals)
        removable = sum(len(g) - 1 for g in screened)
        limit = max(1, int(len(cases) * _removal_ratio()))
        if removable > limit:
            return [], [_removal_refusal(removable, len(cases), limit)]
        return screened, refusals
    except _UNTRUSTED_ERRORS:
        logger.warning(
            "screen_duplicate_groups failed -- honouring no group", exc_info=True
        )
    return [], [_UNSCREENED_NOTE]

"""Applying an already-screened duplicate review: the only code that removes cases."""

from __future__ import annotations

from agents.host_dedup_agree import _group_indices
from agents.host_dedup_consts import _MAX_DUP_NOTES, _UNTRUSTED_ERRORS, logger
from agents.host_dedup_limits import _dup_keeper_key
from tools.rtm import normalize_ac_id


def _plan_drops(cases: list, groups: list) -> dict:
    """``{case index: keeper tc_id}`` for every non-keeper member of each group."""
    drop: dict = {}
    keepers: set = set()
    for members in groups or []:
        idxs = _group_indices(cases, members)
        if len(idxs) < 2:
            continue
        keep_idx = min(((i, cases[i]) for i in idxs), key=_dup_keeper_key)[0]
        keepers.add(keep_idx)
        for i in idxs:
            if i != keep_idx and i not in drop and i not in keepers:
                drop[i] = cases[keep_idx].tc_id
    return drop


def _rescue_sole_requirements(cases: list, drop: dict, notes: list) -> None:
    """Un-drop (in place) any case that is the only one tracing its requirement."""

    def _req(idx: int) -> str:
        return normalize_ac_id(getattr(cases[idx], "requirement_id", "") or "")

    covered = {_req(i) for i in range(len(cases)) if i not in drop and _req(i)}
    for i in sorted(drop):
        req = _req(i)
        if req and req not in covered:
            covered.add(req)
            drop.pop(i)
            if len(notes) < _MAX_DUP_NOTES:
                notes.append(
                    f"`{cases[i].tc_id}` was KEPT despite being grouped as a "
                    f"duplicate: it is the only case tracing {req}."
                )


def _log_removed(removed: int, total: int) -> None:
    if removed:
        logger.info(
            "host-reviewed dedup removed %d near-duplicate case(s) of %d",
            removed,
            total,
        )


# REMOVE the non-keeper members of each ALREADY-SCREENED duplicate group.
#
# ``groups`` MUST be the output of ``screen_duplicate_groups`` -- this function
# applies no safety bound of its own. Only ever called when BOTH
# QA_HOST_DEDUP_REVIEW_ENABLED and QA_HOST_DEDUP_APPLY are on; the default
# behaviour is flag-only. MUST run BEFORE ``_finalize_generation``, which renumbers
# tc_ids. Returns ``(kept_cases, removed, notes)`` where removed is a list of
# ``(removed_tc_id, keeper_tc_id)`` pairs and notes discloses each rescue.
#
# NB-016 mirror (see ``agents.test_scenario_agent._dedupe_cases``): a
# member is NEVER removed while it is the only case
# tracing its ``requirement_id`` -- dropping it would flip that AC to a false
# ORPHAN in the RTM / AC-anchoring reports, which are built afterwards. Ids are
# compared through ``rtm.normalize_ac_id`` (``_dedupe_cases`` compares raw
# strings; the normalised form is the stricter of
# the two).
#
# This rescue is NOT a security bound and is not claimed as one: ``requirement_id``
# is a field of the HOST-SUBMITTED case, so a host emitting ``null`` (the common
# case) or the same id on every case rescues nothing. It defends against bad
# JUDGEMENT, not against a hostile submission -- ``screen_duplicate_groups`` does
# that. It also protects ``requirement_id`` ONLY, not atomic-checklist items; see
# the plan's checklist-interaction note.
#
# Never raises; on any failure every case is kept.
def apply_duplicate_groups(cases: list, groups: list) -> tuple[list, list, list]:
    """REMOVE the non-keeper members of each ALREADY-SCREENED group; never raises."""
    notes: list = []
    if not cases or not groups:
        return list(cases), [], notes
    try:
        drop = _plan_drops(cases, groups)
        _rescue_sole_requirements(cases, drop, notes)
        kept = [tc for i, tc in enumerate(cases) if i not in drop]
        if not kept:  # unreachable (a keeper is always kept); belt-and-braces
            return list(cases), [], notes
        removed = [(cases[i].tc_id, drop[i]) for i in sorted(drop)]
        _log_removed(len(removed), len(cases))
        return kept, removed, notes
    except _UNTRUSTED_ERRORS:
        logger.warning(
            "apply_duplicate_groups failed -- keeping every case", exc_info=True
        )
        return list(cases), [], notes

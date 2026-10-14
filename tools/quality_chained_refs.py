"""Part of the quality-check split (see tools/quality_checks.py): chained refs."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial

from tools.models import TestCase

logger = logging.getLogger("tools.quality_checks")


def _relink_item(it, lookup: dict[str, str], log_dangling: Callable):
    """The item with its chained ``chained_from`` rewritten through ``lookup``."""
    if not (it.strategy == "chained" and it.chained_from):
        return it
    target = lookup.get(it.chained_from)
    if target is None:
        log_dangling(it)
        # genpipe L3: a dangling chained ref is no longer a chain -
        # downgrade it to a plain static value for every consumer.
        return it.model_copy(update={"chained_from": None, "strategy": "static"})
    if target != it.chained_from:
        return it.model_copy(update={"chained_from": target})
    return it


def _relink(tc: TestCase, lookup: dict[str, str], log_dangling: Callable) -> TestCase:
    """Rewrite each chained ``chained_from`` of one case through ``lookup``."""
    if not tc.test_data:
        return tc
    log = partial(log_dangling, tc)
    new_items = [_relink_item(it, lookup, log) for it in tc.test_data]
    if all(new is old for new, old in zip(new_items, tc.test_data)):
        return tc
    return tc.model_copy(update={"test_data": new_items})


def _log_unresolved(tc: TestCase, it) -> None:
    logger.info(
        "resolve_chained_refs_to_stable: clearing chained_from "
        "%r on %s field %r — no such case in category",
        it.chained_from,
        tc.tc_id,
        it.field,
    )


def _log_dropped(tc: TestCase, it) -> None:
    logger.info(
        "restore_chained_refs_from_stable: clearing dangling "
        "chained_from on %s field %r (target case dropped)",
        tc.tc_id,
        it.field,
    )


# Called per CategoryResult BEFORE cross-category flatten, while a tc_id still
# identifies one case WITHIN its own category (every category numbers from
# TC-001). The LLM only sees its own category's ids, so a chained ref can only
# mean a case in the same batch: resolve it to that case's content stable_id,
# which survives flatten, dedup and the final renumber, and clear any ref that
# names no case in this category. Returns a new list; never mutates.
def resolve_chained_refs_to_stable(cases: list[TestCase]) -> list[TestCase]:
    """Rewrite category-local ``chained_from`` tc_ids to the target's stable_id."""
    try:
        local = {tc.tc_id: tc.stable_id for tc in cases}
        return [_relink(tc, local, _log_unresolved) for tc in cases]
    except Exception:
        logger.exception(
            "resolve_chained_refs_to_stable failed — returning cases unchanged"
        )
        return cases


# Counterpart to resolve_chained_refs_to_stable, run AFTER the final TC-001..N
# renumber: each chained_from holds the target's stable_id; map it to that
# case's final tc_id. A stable_id no longer present (target deduped/dropped) is
# a dangling ref, cleared to None and logged so testers never see a wrong
# prerequisite pointer. New list; never raises.
def restore_chained_refs_from_stable(cases: list[TestCase]) -> list[TestCase]:
    """Rewrite stable_id ``chained_from`` values back to the final tc_id."""
    try:
        by_stable = {tc.stable_id: tc.tc_id for tc in cases}
        return [_relink(tc, by_stable, _log_dropped) for tc in cases]
    except Exception:
        logger.exception(
            "restore_chained_refs_from_stable failed — returning cases unchanged"
        )
        return cases

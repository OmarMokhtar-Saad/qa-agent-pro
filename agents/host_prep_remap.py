"""Chained test-data reference remap for the merged-suite renumber."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

# [remap_chained_from]
# Operates on RAW DICTS: at merge time the cases are still the JSON the host
# submitted, so tools/quality_checks.resolve_chained_refs_to_stable -- which
# needs a validated TestCase and its stable_id -- cannot be used yet. The two
# are complementary: this one carries a ref across the MERGE renumber (per
# category, where a tc_id is unambiguous), the other across the FINAL
# risk-order renumber (whole suite, tc_ids already globally unique).
#
# Replaces the test_data LIST with fresh item dicts rather than editing items
# in place. merge_category_rows copies each case with dict(c), which is
# SHALLOW -- the item dicts are still the caller's, straight out of the
# submitted row payload -- so an in-place edit reached back into the caller's
# rows and a second merge over those same rows saw refs this pass had already
# rewritten, and cleared them outright. No live path merges the same rows
# twice today (prep_store.load_submissions re-reads from SQLite on every call,
# and the merge sites are mutually exclusive branches), which is why nothing
# failed; a merge that quietly edits its input is a trap for the next caller.


def remap_chained_from(case: dict, row_map: dict) -> None:
    """Rewrite one merged case's ``chained_from`` ids through *row_map*, in place.

    A ref naming no case in this category is cleared and its strategy
    downgraded to ``static``, so no exporter renders a broken prerequisite.
    Never raises: a disclosure- or cosmetic-level field must not be able to
    fail a submit."""
    with never_raise(logger, "merge: chained_from remap failed", exc_info=True):
        items = case.get("test_data")
        if not isinstance(items, list):
            return
        tc_id = case.get("tc_id")
        remapped = [remap_chained_item(i, row_map, tc_id) for i in items]
        if any(r is not None for r in remapped):
            case["test_data"] = [
                i if r is None else r for i, r in zip(items, remapped, strict=True)
            ]


def _chained_ref(item: object) -> "tuple[dict, str] | None":
    """(item, ref) for a chained dict item with a non-empty ref, else None."""
    if not isinstance(item, dict) or item.get("strategy") != "chained":
        return None
    ref = item.get("chained_from")
    return (item, ref) if isinstance(ref, str) and ref else None


def remap_chained_item(item: object, row_map: dict, tc_id: object) -> "dict | None":
    """A copy of one chained ``test_data`` item with its ref remapped through
    *row_map*, or None when the item is not a chained ref and stays untouched."""
    found = _chained_ref(item)
    if found is None:
        return None
    item, ref = found
    mapped = row_map.get(ref)
    item = dict(item)
    if mapped is None:
        logger.info(
            "merge: clearing chained_from %r on %s -- no such case in its own category",
            ref,
            tc_id,
        )
        item["chained_from"] = None
        item["strategy"] = "static"
    else:
        item["chained_from"] = mapped
    return item

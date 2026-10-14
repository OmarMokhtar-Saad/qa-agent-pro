"""Staged per-category rows -> one merged suite dict, plus the fan-out gate."""

from __future__ import annotations

import logging

from agents import host_category_jobs
from agents.host_category_jobs import normalize_category
from agents.host_prep_remap import remap_chained_from
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

# [fanout_incomplete_note]
# ONE decision point shared by BOTH staged finalize routes -- the bare
# suite_json="" merge AND the review-SIDECAR merge -- because a sidecar
# finalize builds exactly the same merged suite from the same rows and deletes
# the prep on success. The sidecar branch used to be ungated (it only checked
# "are there any rows at all"), so a host that crashed after staging 5 of 8
# categories and then followed the "finalize with a review sidecar"
# instruction shipped a silently truncated 5/8 suite -- precisely the loss
# this gate exists to prevent. On an unexpected error it fails OPEN (returns
# "") so a legitimate finalize is never blocked by the guard itself.
#
# [merge_category_rows]
# 2026-08-30, review round 2 (C1/H2): the renumber is what used to break a
# chained test-data reference, and it had to be fixed here rather than
# downstream. chained_from holds a tc_id the host wrote, valid only inside its
# own category; the renumber rewrites every tc_id to a global TC-0001..N
# sequence and left chained_from alone, so downstream the id named either
# nothing (cleared, prerequisite lost) or -- worse, when the host emitted
# 4-digit ids -- an UNRELATED case in another category. Remapped per ROW, so a
# Negative case's ref to its own TC-001 can never land on the Positive
# category's TC-001. A ref naming no case in its own category is cleared and
# downgraded to static: a dangling chain must never reach an exporter as a
# broken prerequisite.


def fanout_incomplete_note(meta: object, rows: list, prep_id: str) -> str:
    """Refusal text when a parallel-fan-out prep's staged set is INCOMPLETE.

    Returns "" when this prep never requested fan-out, or every expected
    category is staged. Never raises; fails OPEN (returns "")."""
    # pragma: no cover - defensive, must never block a finalize
    with never_raise(logger, "fan-out completeness gate failed", exc_info=True):
        if not isinstance(meta, dict) or not meta.get("parallel_fanout"):
            return ""
        status = _fanout_status(meta, rows)
        if status.get("ready"):
            return ""
        return _incomplete_text(status, prep_id)
    return ""


def _fanout_status(meta: dict, rows: list) -> dict:
    """The category-completeness status of the staged *rows*."""
    expected = list(meta.get("expected_categories") or [])
    staged_names = [
        str(r.get("category_name") or "") for r in rows if isinstance(r, dict)
    ]
    return host_category_jobs.prep_status_view(
        expected=expected, staged_raw_names=staged_names
    )


def _incomplete_text(status: dict, prep_id: str) -> str:
    """The refusal paragraph naming the missing categories."""
    missing = ", ".join(f"`{m}`" for m in (status.get("missing") or [])) or "(unknown)"
    return (
        # D3 (2026-08-21): this refusal used to name the retired fan-out
        # workflow, which this server no longer asks for anywhere (the old
        # wording is deliberately not quoted back here -- see the batch
        # reply above). The GATE is unchanged: it is the crash-safety half
        # of why the staged route was kept.
        "⚠️ Incomplete staged submission: not every expected "
        f"category is staged for prep_id `{prep_id}`.\n\n"
        f"Staged {status.get('staged_count', 0)}/"
        f"{status.get('expected_count', 0)}. Missing: {missing}.\n\n"
        "Call `qa_submit_category` for each missing category "
        "(or submit a full merged suite_json), then finalize "
        "again. `qa_prep_status` shows the current set."
    )


def merge_category_rows(rows: list) -> "tuple[dict, int, dict]":
    """Merge accumulated per-category submission rows into ONE suite dict.

    Rows arrive in insertion order, each payload carrying a validated
    ``test_cases`` list. tc_ids are RENUMBERED to a global unique sequence
    (every category restarts at TC-001 and TestSuite REJECTS duplicates);
    genuine CONTENT duplicates are removed later by _dedupe_cases. Returns
    (merged_suite_dict, rows_used, id_map) where id_map maps each pre-merge
    tc_id to the global TC-NNNN assigned here."""
    merged_cases, case_row, used = collect_category_cases(rows)
    renumbered, id_map, row_maps = _renumber(merged_cases, case_row)
    for idx, c in enumerate(renumbered):
        remap_chained_from(c, row_maps.get(case_row[idx]) or {})
    return {"test_cases": renumbered}, used, id_map


def _renumber(cases: list, case_row: list) -> "tuple[list, dict, dict]":
    """The cases under global TC-NNNN ids, the old->new id map, per-row maps."""
    renumbered: list = []
    id_map: dict = {}
    row_maps: dict[int, dict] = {}
    for i, c in enumerate(cases, 1):
        c = dict(c)
        old_id = c.get("tc_id")
        new_id = f"TC-{i:04d}"
        if isinstance(old_id, str) and old_id:
            id_map.setdefault(old_id, new_id)
            row_maps.setdefault(case_row[i - 1], {})[old_id] = new_id
        c["tc_id"] = new_id
        renumbered.append(c)
    return renumbered, id_map, row_maps


def collect_category_cases(rows: list) -> "tuple[list, list[int], int]":
    """The usable cases of the staged rows, each stamped with its server-derived
    category. Returns (cases, row index of each case, rows used)."""
    merged_cases: list = []
    case_row: list[int] = []
    used = 0
    for row in rows or []:
        cases = _usable_cases(row)
        if cases is None:
            continue
        used += 1
        canon = normalize_category(row.get("category_name", ""))
        for c in cases:
            if isinstance(c, dict):
                merged_cases.append(_stamp_category(c, canon))
                # Which ROW (category) this case came from: a host's
                # chained_from names a case in ITS OWN category, so the old->new
                # id mapping is only unambiguous per row.
                case_row.append(used - 1)
    return merged_cases, case_row, used


def _usable_cases(row: object) -> "list | None":
    """The ``test_cases`` list of a usable staged row, else None."""
    if not isinstance(row, dict):
        return None
    if row.get("category_name", "") == "__round__":  # reserved-key guard
        return None
    cases = (row.get("payload") or {}).get("test_cases")
    return cases if isinstance(cases, list) else None


def _stamp_category(case: dict, canon: "str | None") -> dict:
    """A copy of *case* stamped with the server-derived category.

    F6: the row's category_name is SERVER-DERIVED so it overrides whatever a
    case claims about itself, unconditionally (even when unresolvable), else
    the host's own claim survives on the path called server-derived."""
    case = dict(case)
    case["category"] = canon or None
    case["category_source"] = "server" if canon else None
    return case

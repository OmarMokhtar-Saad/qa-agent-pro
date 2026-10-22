"""The capped, counted duplicate shortlist builders and the case adapter."""

from __future__ import annotations

from agents.host_dedup_consts import logger
from agents.host_dedup_pairs import (
    _DUP_SHORTLIST_MAX_PAIRS,
    _shortlist_entries,
    _shortlist_pairs,
)
from config.settings_guard import never_raise


# Candidate duplicate PAIRS over the merged, renumbered cases, WITH
# the uncapped total.
#
# Returns ``(pairs, total)``: *pairs* truncated to
# _DUP_SHORTLIST_MAX_PAIRS, *total* how many cleared the threshold BEFORE
# that truncation. D4 (2026-08-21) split this out of
# ``build_dup_shortlist``, which is now a thin wrapper carrying its exact
# previous contract, so a caller that RENDERS the list can NAME the
# shortfall when the cap binds instead of quietly showing 12 of N -- this
# repo's no-silent-caps rule, and not a hypothetical: the F08 replay
# recorded above was itself misread once because a saturated 12-pair list
# was hiding nine more. NOTHING about the similarity measure changed --
# there is still exactly ONE implementation and F08's constants are
# untouched, because the F08 note above rejected threshold-tuning on
# measurement and the D4 replay reproduced that result on a second suite
# (see the D4/D5 plan for the sweep).
#
# The original contract, unchanged:
#
# Pure, synchronous, stdlib only -- no LLM, no I/O.
# Deterministic and bounded: at most _DUP_SHORTLIST_MAX_CASES cases are
# compared, a size prefilter skips cheap non-matches, and at most
# _DUP_SHORTLIST_MAX_PAIRS pairs are returned, highest agreement first.
# UNTRUSTED input tolerated (host-authored dicts); never raises, returns []
# on anything unusable. Output rows: {id_a, title_a, id_b, title_b, ratio}
# where the ids are POST-MERGE GLOBAL tc_ids and ``ratio`` is now the Jaccard
# overlap of the two TITLE token sets -- see the F08 note on the constants
# above for why the character-level difflib score was dropped.
def build_dup_shortlist_counted(merged_cases: list) -> tuple[list, int]:
    """Candidate duplicate PAIRS over the merged, renumbered cases, WITH the uncapped total."""
    # public boundary; tests pin that nothing escapes
    with never_raise(logger, "build_dup_shortlist_counted failed", exc_info=True):
        pairs = _shortlist_pairs(_shortlist_entries(merged_cases))
        pairs.sort(key=lambda p: (-p["ratio"], p["id_a"], p["id_b"]))
        return pairs[:_DUP_SHORTLIST_MAX_PAIRS], len(pairs)
    return [], 0


def build_dup_shortlist(merged_cases: list) -> list:
    """The capped pair list only -- the pre-D4 signature and behaviour,
    byte-for-byte. Retained because ``mcp_handlers.dup_shortlist_note``
    (the qa_submit_category call site) and the F08 tests are written
    against it. See ``build_dup_shortlist_counted``."""
    return build_dup_shortlist_counted(merged_cases)[0]


# Adapt FINALIZED suite cases to the JSON-native shape the prescreen reads.
#
# D4 (2026-08-21) -- WHY THIS EXISTS. The prescreen was written for the
# per-category path, where ``mcp_handlers.merge_category_rows`` already hands
# it plain dicts. On the MERGED finalize path the cases are ``TestCase`` model
# objects, and they are read AFTER ``_finalize_generation`` -- deliberately,
# because that call renumbers every ``tc_id``, so these are the FINAL ids that
# match the exported workbook. Reading them any earlier would print ids that
# send the tester to the wrong rows.
#
# Accepts either shape. Pure, synchronous, stdlib only. Never raises; returns
# [] on anything unusable.
def dup_shortlist_cases_json(cases: list) -> list:
    """Adapt FINALIZED suite cases to the JSON-native shape the prescreen reads."""
    out: list = []
    # public boundary; tests pin that nothing escapes
    with never_raise(logger, "dup_shortlist_cases_json failed", exc_info=True):
        for c in cases or []:
            if isinstance(c, dict):
                tid, title = c.get("tc_id"), c.get("title")
            else:
                tid, title = getattr(c, "tc_id", ""), getattr(c, "title", "")
            if tid:
                out.append({"tc_id": str(tid), "title": str(title or "")})
    return out

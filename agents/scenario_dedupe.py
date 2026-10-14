"""Duplicate-case removal that keeps requirement tracers."""

from __future__ import annotations

import logging

from tools.models import TestCase

logger = logging.getLogger("agents.test_scenario_agent")


# Content-identity key for dedup: normalized title + normalized steps.
#
# NB-017/B-027: keying on the title alone collapsed legitimately-distinct cases
# that happen to share a title across categories (e.g. "Submit with empty form"
# from Negative AND Boundary, which differ in their steps). We instead key on a
# hash of the normalized title PLUS every step's action/test_data/expected so two
# cases with the same title but different steps BOTH survive, while true
# duplicates (identical title AND steps) are still collapsed. tc.stable_id is
# exactly this content hash (models._compute_stable_id), so we reuse it.
def _dedupe_stable_key(tc: TestCase) -> str:
    """Content-identity key for dedup: normalized title + normalized steps."""
    return tc.stable_id


# NB-016: dedup runs before the RTM is built, so dropping the only kept case that
# carries requirement_id == AC-00X would flip that AC to a false ORPHAN. When a
# case is about to be dropped as a duplicate, keep it anyway if its requirement_id
# is non-null and not yet covered by an already-kept case — preserving at least
# one tracer per requirement.
def _keep_case(tc: TestCase, seen_keys: set[str], covered_reqs: set[str]) -> bool:
    key = _dedupe_stable_key(tc)
    req = (tc.requirement_id or "").strip() or None
    if key not in seen_keys:
        seen_keys.add(key)
        if req:
            covered_reqs.add(req)
        return True
    # Duplicate by content. Only keep it if it is the last tracer for its
    # requirement_id (would otherwise orphan that AC).
    if req and req not in covered_reqs:
        covered_reqs.add(req)
        logger.debug("Dedup kept a content-duplicate to preserve tracer for %s", req)
        return True
    return False


# NB-016 RESIDUAL (test-data-strategy): the NB-016 keep-exception preserves
# content-identical cases when they carry distinct requirement_ids. A chained_from
# ref targeting such a case (by stable_id) may resolve to EITHER kept duplicate in
# restore_chained_refs_from_stable's by_stable dict (which maps stable_id → tc_id
# and silently overwrites the first with the second). The duplicates are
# content-identical, so picking one arbitrarily is harmless and acceptable.
def _dedupe_cases(all_cases: list[TestCase]) -> list[TestCase]:
    """Drop true duplicates (same title AND steps), never the last tracer (NB-016)."""
    seen_keys: set[str] = set()
    covered_reqs: set[str] = set()
    return [tc for tc in all_cases if _keep_case(tc, seen_keys, covered_reqs)]

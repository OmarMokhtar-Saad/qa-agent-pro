"""AC-anchoring classification, hallucinated-AC filter and advisory section."""

from __future__ import annotations

import logging

from tools.models import TestCase
from tools.rtm import AcceptanceCriterion, normalize_ac_id

# literal name: log records stay under the pre-split module name
logger = logging.getLogger("tools.ac_anchor")


def _real_ac_ids(source_acs: list[AcceptanceCriterion]) -> set[str]:
    return {normalize_ac_id(ac.ac_id) for ac in source_acs if ac and ac.ac_id}


def _bucket_of(tc: TestCase, real: set[str]) -> str:
    rid = normalize_ac_id(getattr(tc, "requirement_id", "") or "")
    if not rid:
        return "unanchored"
    return "anchored" if rid in real else "hallucinated"


def _all_anchored(cases: list[TestCase]) -> dict:
    ids = [tc.tc_id for tc in cases]
    return {"anchored": ids, "hallucinated": [], "unanchored": []}


# Split cases into anchored / hallucinated / unanchored by requirement_id.
#
# - anchored: requirement_id maps to a REAL source AC id.
# - hallucinated: requirement_id is set but maps to NO real AC id.
# - unanchored: requirement_id is empty/None.
#
# Returns a dict of tc_id lists. Never raises.
def classify_cases(
    cases: list[TestCase], source_acs: list[AcceptanceCriterion]
) -> dict:
    """Split cases into anchored / hallucinated / unanchored tc_id lists."""
    real = _real_ac_ids(source_acs)
    buckets: dict[str, list[str]] = {
        "anchored": [],
        "hallucinated": [],
        "unanchored": [],
    }
    try:
        for tc in cases:
            buckets[_bucket_of(tc, real)].append(tc.tc_id)
    except Exception:
        logger.exception("classify_cases failed — treating all as anchored")
        return _all_anchored(cases)
    return buckets


def _is_hallucinated(tc: TestCase, real: set[str]) -> bool:
    rid = normalize_ac_id(getattr(tc, "requirement_id", "") or "")
    return bool(rid and rid not in real)


# Drop cases that cite a NON-EXISTENT AC id (hallucinated traceability).
#
# Only hallucinated cases are dropped — a null requirement_id is KEPT (the
# model simply didn't tag it, which is legitimate). Never empties the suite:
# if dropping would remove every case, the input is returned unchanged. Never
# raises.
def filter_unanchored_cases(
    cases: list[TestCase], source_acs: list[AcceptanceCriterion]
) -> list[TestCase]:
    """Drop cases that cite a NON-EXISTENT AC id; never empties the suite."""
    try:
        if not source_acs:
            return cases
        real = _real_ac_ids(source_acs)
        kept = [tc for tc in cases if not _is_hallucinated(tc, real)]
        if not kept:
            logger.warning("AC anchoring would empty the suite — keeping all cases")
            return cases
        dropped = len(cases) - len(kept)
        if dropped:
            logger.info("AC anchoring dropped %d hallucinated-AC case(s)", dropped)
        return kept
    except Exception:
        logger.exception("filter_unanchored_cases failed — keeping all cases")
        return cases


def _bullet(label: str, ids: list[str]) -> str:
    return f"- **{label}:** " + ", ".join(ids[:20]) + (" …" if len(ids) > 20 else "")


def _header(n_acs: int) -> list[str]:
    return [
        "\n\n## AC Anchoring (advisory)",
        "",
        "These test cases are not traceable to any of the "
        f"{n_acs} acceptance criteria in the source ticket. "
        "Review them — they may test behaviour the ticket does not specify:",
    ]


def _bullets(buckets: dict) -> list[str]:
    out: list[str] = []
    if buckets["hallucinated"]:
        label = "Cite a non-existent AC id (hallucinated)"
        out.append(_bullet(label, buckets["hallucinated"]))
    if buckets["unanchored"]:
        out.append(_bullet("No acceptance criterion cited", buckets["unanchored"]))
    return out


def anchoring_warning_section(
    cases: list[TestCase], source_acs: list[AcceptanceCriterion]
) -> str:
    """Advisory markdown flagging cases not traceable to a REAL source AC.

    Returns "" when there are no source ACs (nothing to anchor to) or when every
    case is anchored. Never raises.
    """
    try:
        if not source_acs or not cases:
            return ""
        buckets = classify_cases(cases, source_acs)
        if not buckets["hallucinated"] and not buckets["unanchored"]:
            return ""
        return "\n".join(_header(len(source_acs)) + _bullets(buckets))
    except Exception:
        logger.exception("anchoring_warning_section failed — returning empty string")
        return ""

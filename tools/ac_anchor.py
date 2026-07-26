"""AC-anchoring check (SHYJ-7154 Fix 3).

When a source ticket carries REAL, human-authored acceptance criteria, every
generated test case should trace back to one of them. Cases that cite a
non-existent AC id (hallucinated traceability) or cite none at all are
"unanchored". This module flags them (advisory) and can optionally drop the
hallucinated ones (QA_AC_ANCHORING_ENFORCE), never emptying the suite.

Only meaningful when the ACs were parsed from the SOURCE content — synthesized
ACs are not a ground-truth anchor, so the caller passes only source ACs here.

Never raises — every helper degrades to a benign result so it can never break
generation.
"""

from __future__ import annotations

import logging

from tools.models import TestCase
from tools.rtm import AcceptanceCriterion, normalize_ac_id

logger = logging.getLogger(__name__)


def _real_ac_ids(source_acs: list[AcceptanceCriterion]) -> set[str]:
    return {normalize_ac_id(ac.ac_id) for ac in source_acs if ac and ac.ac_id}


def classify_cases(
    cases: list[TestCase], source_acs: list[AcceptanceCriterion]
) -> dict:
    """Split cases into anchored / hallucinated / unanchored by requirement_id.

    - anchored: requirement_id maps to a REAL source AC id.
    - hallucinated: requirement_id is set but maps to NO real AC id.
    - unanchored: requirement_id is empty/None.

    Returns a dict of tc_id lists. Never raises.
    """
    real = _real_ac_ids(source_acs)
    anchored: list[str] = []
    hallucinated: list[str] = []
    unanchored: list[str] = []
    try:
        for tc in cases:
            rid = normalize_ac_id(getattr(tc, "requirement_id", "") or "")
            if not rid:
                unanchored.append(tc.tc_id)
            elif rid in real:
                anchored.append(tc.tc_id)
            else:
                hallucinated.append(tc.tc_id)
    except Exception:
        logger.exception("classify_cases failed — treating all as anchored")
        return {
            "anchored": [tc.tc_id for tc in cases],
            "hallucinated": [],
            "unanchored": [],
        }
    return {
        "anchored": anchored,
        "hallucinated": hallucinated,
        "unanchored": unanchored,
    }


def filter_unanchored_cases(
    cases: list[TestCase], source_acs: list[AcceptanceCriterion]
) -> list[TestCase]:
    """Drop cases that cite a NON-EXISTENT AC id (hallucinated traceability).

    Only hallucinated cases are dropped — a null requirement_id is KEPT (the
    model simply didn't tag it, which is legitimate). Never empties the suite:
    if dropping would remove every case, the input is returned unchanged. Never
    raises.
    """
    try:
        if not source_acs:
            return cases
        real = _real_ac_ids(source_acs)
        kept: list[TestCase] = []
        for tc in cases:
            rid = normalize_ac_id(getattr(tc, "requirement_id", "") or "")
            if rid and rid not in real:
                continue  # hallucinated AC id — drop
            kept.append(tc)
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
        hallucinated = buckets["hallucinated"]
        unanchored = buckets["unanchored"]
        if not hallucinated and not unanchored:
            return ""
        lines = [
            "\n\n## AC Anchoring (advisory)",
            "",
            "These test cases are not traceable to any of the "
            f"{len(source_acs)} acceptance criteria in the source ticket. "
            "Review them — they may test behaviour the ticket does not specify:",
        ]
        if hallucinated:
            lines.append(
                "- **Cite a non-existent AC id (hallucinated):** "
                + ", ".join(hallucinated[:20])
                + (" …" if len(hallucinated) > 20 else "")
            )
        if unanchored:
            lines.append(
                "- **No acceptance criterion cited:** "
                + ", ".join(unanchored[:20])
                + (" …" if len(unanchored) > 20 else "")
            )
        return "\n".join(lines)
    except Exception:
        logger.exception("anchoring_warning_section failed — returning empty string")
        return ""

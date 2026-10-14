"""Partition a suite by ``ungrounded`` verdicts, with the proportional bound (leaf of grounding_verdicts)."""

from __future__ import annotations

import logging

from tools.grounding_verdicts_types import UNGROUNDED, Routing, Verdict, _tc_id
from tools.models import TestCase

logger = logging.getLogger("tools.grounding_verdicts")

# The proportional bound. Corpus-independent and deliberately WITHOUT an .env
# knob, for the same reason host_mode._DUP_MAX_APPLY_GROUP_SIZE has none: it is
# derived from the design (a review that re-files most of a suite is not a review
# of that suite), not from a corpus, so there is nothing for an operator to tune
# and nothing to weaken.
_GROUNDING_ROUTE_RATIO_CEILING = 0.40


def _ratio_refusal(n_present: int, n_cases: int) -> str:
    ratio = n_present / n_cases
    if ratio <= _GROUNDING_ROUTE_RATIO_CEILING:
        return ""
    logger.warning(
        "Refusing a grounding batch: %d/%d ungrounded (%.0f%%)",
        n_present,
        n_cases,
        ratio * 100,
    )
    return (
        f"{n_present} of {n_cases} cases ({ratio:.0%}) were marked "
        f"ungrounded, above the {_GROUNDING_ROUTE_RATIO_CEILING:.0%} ceiling. "
        "No case was moved: a review that re-files most of a suite is not a "
        "review of that suite, and this payload is model-authored and can be "
        "influenced by text inside the ticket. Re-read the ticket and the "
        "flagged cases by hand."
    )


def _empty_suite_refusal(cases: list[TestCase]) -> Routing:
    logger.warning("Grounding routing would empty the suite - keeping all cases")
    return Routing(
        kept=list(cases),
        routed=[],
        refusal=(
            "Every case was marked ungrounded, which would leave nothing to "
            "execute. No case was moved."
        ),
    )


# Partition the suite into executable cases and routed-away ones.
#
# Refuses the WHOLE batch -- routing nothing -- when the ``ungrounded`` share
# exceeds :data:`_GROUNDING_ROUTE_RATIO_CEILING`, or when routing would leave
# no executable case at all. Never raises; on any failure the suite is returned
# untouched, because failing to route is recoverable and losing coverage is not.
def split_ungrounded(cases: list[TestCase], verdicts: list[Verdict]) -> Routing:
    """Partition the suite into executable cases and routed-away ones."""
    try:
        if not cases:
            return Routing(kept=list(cases or []), routed=[])
        marked = {v.tc_id for v in verdicts or [] if v.verdict == UNGROUNDED}
        present = [tc for tc in cases if _tc_id(tc) in marked]
        if not present:
            return Routing(kept=list(cases), routed=[])
        reason = _ratio_refusal(len(present), len(cases))
        if reason:
            return Routing(kept=list(cases), routed=[], refusal=reason)
        kept = [tc for tc in cases if _tc_id(tc) not in marked]
        if not kept:
            return _empty_suite_refusal(cases)
        return Routing(kept=kept, routed=present)
    except Exception:
        logger.exception("split_ungrounded failed - keeping the suite unchanged")
        return Routing(kept=list(cases or []), routed=[])

"""Phase 0 granularity audit of an atomic checklist (advisory, never blocks)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools import atomic_checklist_core as _core
from tools import settings_port
from tools.atomic_checklist_core import (
    _MAX_OVERLAP_RATIO,
    _MAX_SHORT_RATIO,
    _MIN_PROVENANCE_RATIO,
    _OVERLAP_SIM,
    _SHORT_ITEM_WORDS,
    ChecklistItem,
)
from tools.atomic_checklist_source import UNATTRIBUTED

logger = logging.getLogger("tools.atomic_checklist")


class _Ratios(NamedTuple):
    short: float
    overlap: float
    provenance: float
    distinct: int
    total: int


def _empty() -> dict:
    return {
        "item_count": 0,
        "pattern_distribution": {},
        "distinct_patterns": 0,
        "short_ratio": 0.0,
        "overlap_ratio": 0.0,
        "provenance_ratio": 0.0,
        "score": 0.0,
        "warnings": [],
        "passed": True,
    }


def _report(r: _Ratios, dist: dict, result: tuple) -> dict:
    score, warnings = result
    threshold = float(
        getattr(settings_port.get(), "qa_checklist_min_granularity", 0.6) or 0.6
    )
    return {
        "item_count": r.total,
        "pattern_distribution": dist,
        "distinct_patterns": len(dist),
        "short_ratio": round(r.short, 3),
        "overlap_ratio": round(r.overlap, 3),
        "provenance_ratio": round(r.provenance, 3),
        "score": round(score, 3),
        "warnings": warnings,
        "passed": score >= threshold,
    }


def _verdict(items: list[ChecklistItem]) -> dict:
    count = len(items)
    dist, short, attributed, overlapping = _audit_granularity_counts(items)
    ratios = _Ratios(
        short=short / count,
        overlap=overlapping / count,
        provenance=attributed / count,
        distinct=len(dist),
        total=count,
    )
    return _report(ratios, dist, _audit_granularity_score(ratios))


# Score the decomposition on 3 dimensions and return an advisory verdict.
#
# Dimensions: (1) EARS pattern distribution, (2) fragment granularity
# (over-short items + semantic overlap), (3) provenance completeness — where
# "attributed" means the source survived the ``normalize_source`` allowlist,
# so a spoofed tag counts AGAINST the score.
#
# Returns ``{"score": float, "warnings": [...], "passed": bool, ...}``. A score
# below ``qa_checklist_min_granularity`` means the checklist is probably
# inflated or under-split; the caller SURFACES that to the tester but never
# hard-blocks generation (house rule: log and degrade). Never raises.
def audit_granularity(items: list[ChecklistItem]) -> dict:
    """Score the decomposition on 3 dimensions; advisory verdict. Never raises."""
    try:
        if not items:
            return _empty()
        return _verdict(items)
    except Exception:
        logger.exception("audit_granularity failed — returning a neutral verdict")
        return _empty()


def _overlap_count(items: list[ChecklistItem]) -> int:
    count = len(items)
    token_sets = [set(_core._tokens(it.text)) for it in items]
    overlapping = 0
    for i in range(count):
        for j in range(i + 1, count):
            if _core._jaccard(token_sets[i], token_sets[j]) >= _OVERLAP_SIM:
                overlapping += 1
                break
    return overlapping


def _audit_granularity_counts(
    items: list[ChecklistItem],
) -> tuple[dict[str, int], int, int, int]:
    """Count the EARS patterns, short items, attributed items and overlapping items."""
    dist: dict[str, int] = {}
    short = 0
    attributed = 0
    for it in items:
        dist[it.ears_pattern] = dist.get(it.ears_pattern, 0) + 1
        if len(_core._tokens(it.text)) < _SHORT_ITEM_WORDS:
            short += 1
        if it.source and it.source != UNATTRIBUTED:
            attributed += 1
    return dist, short, attributed, _overlap_count(items)


def _check_short(r: _Ratios, warnings: list[str]) -> float:
    if r.short > _MAX_SHORT_RATIO:
        warnings.append(
            f"{r.short:.0%} of items are shorter than {_SHORT_ITEM_WORDS} "
            "words — the decomposition looks OVER-SPLIT (inflation raises the "
            "coverage %, it does not improve coverage)."
        )
        return 0.3
    return 0.0


def _check_overlap(r: _Ratios, warnings: list[str]) -> float:
    if r.overlap > _MAX_OVERLAP_RATIO:
        warnings.append(
            f"{r.overlap:.0%} of items are near-duplicates of another item "
            "— redundant requirements make the tally look better than it is."
        )
        return 0.3
    return 0.0


def _check_skew(r: _Ratios, warnings: list[str]) -> float:
    if r.distinct <= 1 and r.total >= 8:
        warnings.append(
            "Every item uses the same EARS pattern — extreme skew usually means "
            "the decomposition restated one shape instead of analysing the "
            "behaviours."
        )
        return 0.2
    return 0.0


def _check_provenance(r: _Ratios, warnings: list[str]) -> float:
    if r.provenance < _MIN_PROVENANCE_RATIO:
        warnings.append(
            f"Only {r.provenance:.0%} of items carry a recognised source "
            "tag — unattributed requirements cannot be audited back to the "
            "ticket."
        )
        return 0.2
    return 0.0


def _audit_granularity_score(r: _Ratios) -> tuple[float, list[str]]:
    """Score the three ratios and the pattern skew, with one warning per penalty."""
    warnings: list[str] = []
    score = 1.0
    for check in (_check_short, _check_overlap, _check_skew, _check_provenance):
        score -= check(r, warnings)
    score = max(0.0, min(1.0, score))
    return score, warnings

"""Detail leaf of the case quality gate: the refusal markdown."""

from __future__ import annotations

import logging

from tools.cqg_types import _KIND_LABELS, KINDS, QualityGateResult

logger = logging.getLogger("tools.case_quality_gate")


def _kind_lines(result: QualityGateResult) -> list:
    lines: list = []
    for kind in KINDS:
        count = result.counts.get(kind, 0)
        if count:
            lines.append(
                "- **" + _KIND_LABELS[kind] + "**: " + str(count) + " finding(s)"
            )
    return lines


def _example_lines(result: QualityGateResult, limit: int) -> list:
    return [
        "  - `" + finding.tc_id + "` (" + finding.category + ") -- " + finding.detail
        for finding in result.examples(limit)
    ]


def detail_lines(result: QualityGateResult, limit: int) -> str:
    """The markdown the refusal quotes: counts per kind, then example ids."""
    try:
        if not getattr(result, "found", False):
            return ""
        return "\n".join(_kind_lines(result) + _example_lines(result, limit))
    except Exception:
        logger.exception("case quality gate detail failed - returning ''")
        return ""

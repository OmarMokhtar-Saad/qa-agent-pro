"""Scan leaf of the case quality gate: rows -> QualityGateResult."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config.settings_guard import never_raise
from tools.cqg_types import (
    DUPLICATE_TITLE,
    EMPTY_TEST_DATA,
    PLACEHOLDER_TEST_DATA,
    RESTATED_EXPECTED,
    QualityFinding,
    QualityGateResult,
    _Enough,
    _text,
    normalize,
)

logger = logging.getLogger("tools.case_quality_gate")

#: Hard bound on one scan. A hostile or broken submission cannot make this
#: module allocate without limit, and the refusal text quotes a handful anyway.
_MAX_FINDINGS = 500


@dataclass
class _Scan:
    result: QualityGateResult
    findings: list
    first_by_title: dict


@dataclass(frozen=True)
class _CaseCtx:
    tc_id: str
    category: str
    findings: list


def _steps_of(case) -> list:
    with never_raise():
        return list(getattr(case, "steps", None) or [])
    return []


def _note_title(scan: _Scan, ctx: _CaseCtx, case) -> None:
    title_key = normalize(_text(case, "title"))
    if title_key:
        first = scan.first_by_title.get(title_key)
        if first is None:
            scan.first_by_title[title_key] = ctx.tc_id
        else:
            detail = "same title as " + first
            ctx.findings.append(
                QualityFinding(DUPLICATE_TITLE, ctx.tc_id, ctx.category, detail)
            )
            if len(ctx.findings) >= _MAX_FINDINGS:
                raise _Enough()


def _scan_step(step, index: int, ctx: _CaseCtx) -> None:
    """Scan one step for placeholder test data and an action restated as its result."""
    data = _text(step, "test_data")
    if data and data.casefold() in PLACEHOLDER_TEST_DATA:
        detail = "step " + str(index) + " has no usable test_data"
        ctx.findings.append(
            QualityFinding(EMPTY_TEST_DATA, ctx.tc_id, ctx.category, detail)
        )
    action = normalize(_text(step, "action"))
    expected = normalize(_text(step, "expected_result"))
    if action and expected and action == expected:
        detail = "step " + str(index) + " restates its action verbatim"
        ctx.findings.append(
            QualityFinding(RESTATED_EXPECTED, ctx.tc_id, ctx.category, detail)
        )


def _scan_case(case, scan: _Scan) -> None:
    """Scan one case; appends to the findings, may raise ``_Enough`` at the cap."""
    scan.result.scanned_cases += 1
    ctx = _CaseCtx(
        tc_id=_text(case, "tc_id") or "(no id)",
        category=_text(case, "category") or "(uncategorised)",
        findings=scan.findings,
    )
    _note_title(scan, ctx, case)
    for index, step in enumerate(_steps_of(case), 1):
        _scan_step(step, index, ctx)
        if len(ctx.findings) >= _MAX_FINDINGS:
            raise _Enough()


def _tally(result: QualityGateResult, findings: list) -> QualityGateResult:
    result.findings = findings
    for finding in findings:
        result.counts[finding.kind] = result.counts.get(finding.kind, 0) + 1
        result.per_category[finding.category] = (
            result.per_category.get(finding.category, 0) + 1
        )
    return result


def scan_rows(rows: list) -> QualityGateResult:
    """Scan the already-listed cases of the FINAL suite; never raises."""
    scan = _Scan(QualityGateResult(), [], {})
    with never_raise(
        logger,
        "case quality gate failed - reporting nothing",
        level=logging.ERROR,
        exc_info=True,
    ) as g:
        try:
            for case in rows:
                _scan_case(case, scan)
        except _Enough:
            logger.info(
                "case quality gate: stopped at the %d-finding cap", _MAX_FINDINGS
            )
    if g.failed:
        return QualityGateResult()
    return _tally(scan.result, scan.findings)

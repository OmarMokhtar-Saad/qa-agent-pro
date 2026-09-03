"""Submit-time CASE QUALITY gate (2026-08-30 audit, group C2).

Pure, synchronous, stdlib-only. It reads a FINAL merged+renumbered suite and
reports three deterministic defects a tester cannot execute against:

  * ``empty_test_data`` -- a step whose ``test_data`` is one of the placeholder
    tokens testers type in and are not ("N/A", "TBD", "-", "none", "null"). A
    genuinely ABSENT or empty ``test_data`` is NOT flagged: the model documents it
    as legitimate ("Empty for a case that manipulates no data"), so only an
    affirmatively-typed useless value is the defect.
  * ``restated_expected`` -- a step whose ``expected_result``, after
    normalisation, EQUALS its ``action``. Such a step passes against correct and
    against broken software, so executing it measures nothing. This is the EXACT
    restatement only; the graded, ratio-based signal lives in
    ``tools/step_assertion.py`` and is deliberately not duplicated here.
  * ``duplicate_title`` -- two cases whose normalised titles are identical. The
    eight categories are generated blind to each other, so the same title in two
    of them is the shape a genuine cross-category duplicate takes.

Contract, identical to every other never-raise detector in this tree: the input
is UNTRUSTED (it came from the tester's own chat model), every field is read
through ``getattr``/``str`` rather than assumed, and ANY failure yields an EMPTY
result -- never an exception, never a partial verdict. An empty result must leave
the submit reply byte-identical, which is what makes the gate safe to run on
every submit.

An absent or empty ``test_data`` is model-legitimate and never flagged; only a
typed placeholder token is. This keeps the gate off the many valid cases that
manipulate no data, and needs no attribute-presence probe.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Values that LOOK filled in and are not. Compared case-folded, after stripping.
PLACEHOLDER_TEST_DATA = frozenset({"n/a", "tbd", "-", "none", "null"})

#: Hard bound on one scan. A hostile or broken submission cannot make this
#: module allocate without limit, and the refusal text quotes a handful anyway.
_MAX_FINDINGS = 500
_MAX_EXAMPLES = 5

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

EMPTY_TEST_DATA = "empty_test_data"
RESTATED_EXPECTED = "restated_expected"
DUPLICATE_TITLE = "duplicate_title"

#: Report order, so the detail block is stable across runs.
KINDS = (EMPTY_TEST_DATA, RESTATED_EXPECTED, DUPLICATE_TITLE)

_KIND_LABELS = {
    EMPTY_TEST_DATA: "step carries no concrete test data",
    RESTATED_EXPECTED: "expected_result only restates the action",
    DUPLICATE_TITLE: "duplicate case title",
}


class _Enough(Exception):
    """Internal: the finding cap was reached. Never escapes this module."""


def normalize(text: object) -> str:
    """Case-folded, every non-alphanumeric run collapsed to one space, stripped."""
    try:
        return _NON_ALNUM_RE.sub(" ", str(text or "").casefold()).strip()
    except Exception:  # pragma: no cover - defensive
        return ""


@dataclass(frozen=True)
class QualityFinding:
    """One defect, addressed by the id the tester will read in the export."""

    kind: str
    tc_id: str
    category: str
    detail: str


@dataclass
class QualityGateResult:
    """What one scan found. Empty is the healthy, byte-identical case."""

    findings: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    per_category: dict = field(default_factory=dict)
    scanned_cases: int = 0

    @property
    def found(self) -> bool:
        return bool(self.findings)

    def examples(self, limit: int = _MAX_EXAMPLES) -> list:
        """A few findings, one per KIND first so no defect class is invisible."""
        try:
            cap = max(int(limit), 0)
        except Exception:  # pragma: no cover - defensive
            cap = _MAX_EXAMPLES
        picked: list = []
        seen: set = set()
        for finding in self.findings:
            if finding.kind not in seen:
                seen.add(finding.kind)
                picked.append(finding)
        for finding in self.findings:
            if len(picked) >= cap:
                break
            if finding not in picked:
                picked.append(finding)
        return picked[:cap]


def _text(obj: object, name: str) -> str:
    try:
        return str(getattr(obj, name, "") or "").strip()
    except Exception:  # pragma: no cover - defensive
        return ""


def scan_suite(cases) -> QualityGateResult:
    """Scan the FINAL suite. Never raises; an unusable input reports nothing."""
    result = QualityGateResult()
    try:
        rows = list(cases or [])
    except Exception:
        logger.debug("case quality gate: unusable suite", exc_info=True)
        return QualityGateResult()
    findings: list = []
    first_by_title: dict = {}
    try:
        for case in rows:
            result.scanned_cases += 1
            tc_id = _text(case, "tc_id") or "(no id)"
            category = _text(case, "category") or "(uncategorised)"
            title_key = normalize(_text(case, "title"))
            if title_key:
                first = first_by_title.get(title_key)
                if first is None:
                    first_by_title[title_key] = tc_id
                else:
                    findings.append(
                        QualityFinding(
                            DUPLICATE_TITLE,
                            tc_id,
                            category,
                            "same title as " + first,
                        )
                    )
                    if len(findings) >= _MAX_FINDINGS:
                        raise _Enough()
            try:
                steps = list(getattr(case, "steps", None) or [])
            except Exception:
                steps = []
            for index, step in enumerate(steps, 1):
                data = _text(step, "test_data")
                if data and data.casefold() in PLACEHOLDER_TEST_DATA:
                    findings.append(
                        QualityFinding(
                            EMPTY_TEST_DATA,
                            tc_id,
                            category,
                            "step " + str(index) + " has no usable test_data",
                        )
                    )
                action = normalize(_text(step, "action"))
                expected = normalize(_text(step, "expected_result"))
                if action and expected and action == expected:
                    findings.append(
                        QualityFinding(
                            RESTATED_EXPECTED,
                            tc_id,
                            category,
                            "step " + str(index) + " restates its action verbatim",
                        )
                    )
                if len(findings) >= _MAX_FINDINGS:
                    raise _Enough()
    except _Enough:
        logger.info("case quality gate: stopped at the %d-finding cap", _MAX_FINDINGS)
    except Exception:
        logger.exception("case quality gate failed - reporting nothing")
        return QualityGateResult()
    result.findings = findings
    for finding in findings:
        result.counts[finding.kind] = result.counts.get(finding.kind, 0) + 1
        result.per_category[finding.category] = (
            result.per_category.get(finding.category, 0) + 1
        )
    return result


def gate_detail(result: QualityGateResult, limit: int = _MAX_EXAMPLES) -> str:
    """The markdown the refusal quotes: counts per kind, then example ids."""
    try:
        if not getattr(result, "found", False):
            return ""
        lines: list = []
        for kind in KINDS:
            count = result.counts.get(kind, 0)
            if count:
                lines.append(
                    "- **" + _KIND_LABELS[kind] + "**: " + str(count) + " finding(s)"
                )
        for finding in result.examples(limit):
            lines.append(
                "  - `"
                + finding.tc_id
                + "` ("
                + finding.category
                + ") -- "
                + finding.detail
            )
        return "\n".join(lines)
    except Exception:
        logger.exception("case quality gate detail failed - returning ''")
        return ""

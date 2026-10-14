"""Types, constants and text helpers for the case quality gate."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from config.settings_guard import never_raise

#: Values that LOOK filled in and are not. Compared case-folded, after stripping.
PLACEHOLDER_TEST_DATA = frozenset({"n/a", "tbd", "-", "none", "null"})

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
    # defensive
    with never_raise():
        return _NON_ALNUM_RE.sub(" ", str(text or "").casefold()).strip()
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
        except (TypeError, ValueError, OverflowError):  # pragma: no cover - defensive
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
    # defensive
    with never_raise():
        return str(getattr(obj, name, "") or "").strip()
    return ""

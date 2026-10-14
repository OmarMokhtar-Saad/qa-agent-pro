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

from config.settings_guard import never_raise
from tools.cqg_detail import detail_lines
from tools.cqg_scan import scan_rows
from tools.cqg_types import (  # noqa: F401
    _MAX_EXAMPLES,
    DUPLICATE_TITLE,
    EMPTY_TEST_DATA,
    KINDS,
    PLACEHOLDER_TEST_DATA,
    RESTATED_EXPECTED,
    QualityFinding,
    QualityGateResult,
    normalize,
)

logger = logging.getLogger(__name__)


def scan_suite(cases) -> QualityGateResult:
    """Scan the FINAL suite. Never raises; an unusable input reports nothing."""
    with never_raise(logger, "case quality gate: unusable suite", exc_info=True) as g:
        rows = list(cases or [])
    if g.failed:
        return QualityGateResult()
    return scan_rows(rows)


def gate_detail(result: QualityGateResult, limit: int = _MAX_EXAMPLES) -> str:
    """The markdown the refusal quotes: counts per kind, then example ids."""
    return detail_lines(result, limit)

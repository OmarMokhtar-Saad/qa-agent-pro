"""Per-case ungrounded-span scan (leaf of oracle_grounding)."""

from __future__ import annotations

from tools.models import TestCase
from tools.oracle_grounding_copy import _is_asserted_ui_string
from tools.oracle_grounding_spans import _quoted_spans
from tools.oracle_grounding_text import _case_data_text, _normalize


def _step_spans(
    expected: str | None, known: tuple[str, str], templates: list
) -> list[str]:
    grounding, own_data = known
    out: list[str] = []
    for span in _quoted_spans(expected):
        if not _is_asserted_ui_string(span):
            continue
        normalized = _normalize(span)
        if normalized in grounding or normalized in own_data:
            continue
        # F7: a filled instance of a promised template is promised.
        if any(t.fullmatch(normalized) for t in templates):
            continue
        out.append(span)
    return out


def case_findings(
    tc: TestCase, grounding: str, templates: list
) -> list[tuple[str, int, str]]:
    own_data = _case_data_text(tc)
    out: list[tuple[str, int, str]] = []
    for step in getattr(tc, "steps", None) or []:
        for span in _step_spans(
            getattr(step, "expected_result", None), (grounding, own_data), templates
        ):
            tc_id = getattr(tc, "tc_id", "") or ""
            out.append((tc_id, getattr(step, "step_number", 0) or 0, span))
    return out

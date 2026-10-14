"""One table row -> a TestCase or a named rejection."""

from __future__ import annotations

import re
from typing import NamedTuple

from config.settings_guard import never_raise
from tools.mobile.imp_consts import COLUMN_ALIASES, PRIORITY_LOOKUP, TYPE_LOOKUP
from tools.mobile.imp_expect import _expected_for
from tools.mobile.imp_explain import _explain_validation
from tools.mobile.imp_headers import _split_numbered
from tools.mobile.imp_text import _clean
from tools.models import Priority, TestCase, TestStep, TestType
from tools.untrusted import single_line, strip_spoof_tags


class RowAt(NamedTuple):
    """Where a row sits: its sheet row number and the next fallback case number."""

    number: int
    fallback: int


class _Cell:
    """The cleaned cell of one row, looked up by field name."""

    def __init__(self, row: list[str], mapping: dict[str, int]) -> None:
        self._row = row
        self._mapping = mapping

    def __call__(self, field: str) -> str:
        index = self._mapping.get(field)
        if index is None or index >= len(self._row):
            return ""
        return _clean(self._row[index])


def _outcome(
    case: object = None, reject: dict | None = None, renumbered: bool = False
) -> dict:
    return {"case": case, "reject": reject, "renumbered": renumbered}


def _rejected(row_number: int, why: str, title: str) -> dict:
    # The title is a raw cell, echoed to the host model: short and neutralised.
    shown = strip_spoof_tags(single_line(title, 60))
    return _outcome(reject={"row": row_number, "why": why, "title": shown})


def _resolve_tc_id(raw_id: str, fallback_index: int) -> tuple[str, bool]:
    """The normalised ``TC-nnn`` id and whether it differs from what was written."""
    digits = re.sub(r"\D", "", raw_id)
    if digits and 1 <= len(digits) <= 6:
        tc_id = "TC-" + digits.zfill(3)
        return tc_id, tc_id != raw_id.strip().upper()
    return "TC-" + str(fallback_index).zfill(3), True


class _Parts(NamedTuple):
    title: str
    steps: list[str]
    expected: list[str]


def _steps_and_expected(cell: _Cell) -> tuple[list[str], list[str], str | None]:
    """The numbered steps and expected results, and why a row cannot have them."""
    steps = _split_numbered(cell("steps"))
    if not steps:
        return steps, [], "no steps"
    expected = _split_numbered(cell("expected"))
    return steps, expected, None if expected else "no expected result"


def _build_case(cell: _Cell, tc_id: str, parts: _Parts) -> TestCase:
    title, steps, expected = parts
    module = cell("module") or "Imported cases"
    return TestCase(
        tc_id=tc_id,
        module=module[:100] if len(module) >= 2 else "Imported cases",
        title=title[:250],
        priority=PRIORITY_LOOKUP.get(cell("priority").lower(), Priority.MEDIUM),
        type=TYPE_LOOKUP.get(cell("type").lower(), TestType.FUNCTIONAL),
        preconditions=cell("preconditions") or None,
        steps=[
            TestStep(
                step_number=number,
                action=action,
                expected_result=_expected_for(number - 1, expected, len(steps)),
            )
            for number, action in enumerate(steps, start=1)
        ],
    )


def _row_to_case(row: list[str], mapping: dict[str, int], at: RowAt) -> dict:
    """``{"case": TestCase|None, "reject": {...}|None, "renumbered": bool}``."""
    cell = _Cell(row, mapping)
    title = cell("title")
    if not any(cell(name) for name in COLUMN_ALIASES):
        return _outcome()
    if not title:
        return _rejected(at.number, "no title", "")
    steps, expected, why = _steps_and_expected(cell)
    if why:
        return _rejected(at.number, why, title)

    tc_id, renumbered = _resolve_tc_id(cell("tc_id"), at.fallback)
    with never_raise() as g:
        built = _build_case(cell, tc_id, _Parts(title, steps, expected))
    if g.failed:
        return _rejected(at.number, _explain_validation(g.exc), title)
    return _outcome(case=built, renumbered=renumbered)

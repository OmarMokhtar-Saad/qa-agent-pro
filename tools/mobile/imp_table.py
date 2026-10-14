"""Header row + data rows -> the import result of a table."""

from __future__ import annotations

from typing import Any

from tools.mobile import imp_consts
from tools.mobile.imp_case import RowAt, _row_to_case
from tools.mobile.imp_headers import _map_headers
from tools.mobile.imp_text import fail

_NO_COLUMNS = (
    "This table has no recognisable title and steps columns, so "
    "nothing was imported. Expected headers like: "
    + ", ".join(("TC ID", "Title", "Steps", "Expected Results"))
    + "."
)


def _convert_rows(
    rows: list[list[str]], mapping: dict[str, int], first_data_row: int
) -> tuple[list, list, list]:
    """Data rows -> (cases, rejected, renumbered tc_ids)."""
    cases: list[Any]
    cases, rejected, renumbered = [], [], []
    for offset, row in enumerate(rows[1 : imp_consts.MAX_ROWS + 1]):
        at = RowAt(first_data_row + offset, len(cases) + 1)
        outcome = _row_to_case(row, mapping, at)
        if outcome["case"] is not None:
            cases.append(outcome["case"])
            if outcome["renumbered"]:
                renumbered.append(outcome["case"].tc_id)
        elif outcome["reject"] is not None:
            rejected.append(outcome["reject"])
    return cases, rejected, renumbered


def _no_cases_message(rejected: list[dict]) -> str:
    base = "No row in this table produced a usable test case."
    if not rejected:
        return base
    return (
        base
        + " "
        + str(len(rejected))
        + " row(s) were rejected; the first was row "
        + str(rejected[0]["row"])
        + " ("
        + rejected[0]["why"]
        + ")."
    )


def _content(rows: list[list[str]], converted: tuple[list, list, list]) -> dict:
    cases, rejected, renumbered = converted
    return {
        "cases": cases,
        "rejected": rejected,
        "renumbered": renumbered,
        "total_rows": len(rows) - 1,
        "truncated": len(rows) - 1 > imp_consts.MAX_ROWS,
    }


def _assemble(rows: list[list[str]], first_data_row: int) -> dict:
    """Header row + data rows -> the import result."""
    if not rows:
        return fail("There were no rows to import.")
    mapping = _map_headers(rows[0])
    if "title" not in mapping or "steps" not in mapping:
        return fail(_NO_COLUMNS)
    converted = _convert_rows(rows, mapping, first_data_row)
    if not converted[0]:
        return fail(_no_cases_message(converted[1]))
    return {"error": None, "content": _content(rows, converted)}

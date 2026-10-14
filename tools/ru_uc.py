"""Requirement-unit use-case table: label/value rows and their atomic units."""

from __future__ import annotations

import logging
from collections.abc import Callable

from tools.ru_cells import _label_kind, _split_row
from tools.ru_consts import _MAX_UC_ROWS
from tools.ru_flow import _split_flow, _split_rules
from tools.ru_wrap import _join_wrapped_rows

logger = logging.getLogger("tools.requirement_units")


def _uc_rows(description: str) -> list[tuple[str, str]]:
    """(label, value) pairs from the leading use-case table."""
    rows: list[tuple[str, str]] = []
    try:
        for line in _join_wrapped_rows(description):
            cells = _split_row(line)
            if len(cells) >= 2 and cells[0]:
                rows.append((cells[0], " ".join(c for c in cells[1:] if c)))
            if len(rows) >= _MAX_UC_ROWS:
                logger.info("_uc_rows hit the %d-row cap - truncating", _MAX_UC_ROWS)
                break
    except Exception:
        logger.exception("_uc_rows failed - returning what was parsed")
    return rows


def _add_use_case_units(
    description: str, add: Callable[[str, str, str | None], None]
) -> None:
    """Feed every use-case-table row to ``add``, one call per atomic unit."""
    for label, value in _uc_rows(description):
        kind = _label_kind(label)
        if not kind or not value:
            continue
        if kind in {"basic_flow", "alternative_flow"}:
            for step in _split_flow(value):
                add(kind, step, None)
        elif kind == "business_rule":
            for _declared, body in _split_rules(value):
                add(kind, body, None)
        else:
            add(kind, value, None)

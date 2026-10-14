"""Requirement-unit parsing: ids, the unit cap and the public parse entry point."""

from __future__ import annotations

import logging
from collections.abc import Callable

from tools.ru_cells import _clean_cell
from tools.ru_consts import (
    _KIND_PREFIX,
    _MAX_UNIT_CHARS,
    _MAX_UNITS,
    _MIN_UNIT_CHARS,
)
from tools.ru_model import RequirementUnit
from tools.ru_tables import _add_data_field_units
from tools.ru_uc import _add_use_case_units

logger = logging.getLogger("tools.requirement_units")


def _next_unit_id(counters: dict[str, int], kind: str) -> str:
    """The next ``PRE-n`` / ``BF-n`` ... id for ``kind`` (bumps its counter)."""
    counters[kind] = counters.get(kind, 0) + 1
    return f"{_KIND_PREFIX.get(kind, 'REQ')}-{counters[kind]}"


def _unit_cap_reached(units: list[RequirementUnit]) -> bool:
    """True once ``units`` is full; logs on every over-cap add."""
    if len(units) == _MAX_UNITS:
        logger.info(
            "parse_requirement_units hit the %d-unit cap - "
            "later requirements are not emitted",
            _MAX_UNITS,
        )
    return len(units) >= _MAX_UNITS


def _make_adder(
    units: list[RequirementUnit], counters: dict[str, int], prov: str
) -> Callable[[str, str, str | None], None]:
    """The ``add(kind, text, explicit_id)`` closure that appends cleaned units."""

    def add(kind: str, text: str, explicit_id: str | None = None) -> None:
        body = _clean_cell(text)
        if len(body) < _MIN_UNIT_CHARS or _unit_cap_reached(units):
            return
        unit_id = explicit_id or _next_unit_id(counters, kind)
        units.append(
            RequirementUnit(
                unit_id=unit_id,
                text=body[:_MAX_UNIT_CHARS],
                kind=kind,
                provenance=prov,
            )
        )

    return add


# Produces ``PRE-n`` / ``POST-n`` / ``BF-n`` / ``AF-n`` / ``BR-n`` from the
# use-case table and ``DFnn-n`` from each data-field table row. Returns [] for
# an empty or unparseable description -- callers treat an empty result as "no
# anchorable requirements", exactly as they do for an absent AC field.
# Never raises.
def parse_requirement_units(
    description: str, provenance: str = "target"
) -> list[RequirementUnit]:
    """Atomic requirement units parsed from a use-case-table description."""
    units: list[RequirementUnit] = []
    try:
        if not description or not description.strip():
            return []
        prov = "background" if provenance == "background" else "target"
        add = _make_adder(units, {}, prov)
        _add_use_case_units(description, add)
        _add_data_field_units(description, add)
    except Exception:
        logger.exception("parse_requirement_units failed - returning what was parsed")
    return units


def assignable_unit_ids(units: list[RequirementUnit]) -> set[str]:
    """Unit ids a test case is allowed to cite (target provenance only)."""
    try:
        return {u.unit_id for u in units if u.is_assignable}
    except Exception:
        logger.exception("assignable_unit_ids failed - returning empty set")
        return set()

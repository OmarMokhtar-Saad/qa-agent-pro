"""Requirement-unit data-field tables: DFnn markers, rows and their atomic units."""

from __future__ import annotations

import logging
from collections.abc import Callable

from tools import ru_patterns as rp
from tools.ru_cells import _section_id_match, _split_row
from tools.ru_consts import _MAX_ROWS_PER_TABLE, _MAX_TABLES
from tools.ru_model import DataFieldTable
from tools.ru_wrap import _join_wrapped_rows

logger = logging.getLogger("tools.requirement_units")


def _data_field_row(cells: list[str]) -> tuple[str, str, str] | None:
    """The (ar, en, kind) triple of a data row; None for blank or header rows."""
    meaningful = [c for c in cells if c]
    if not meaningful:
        return None
    head = meaningful[0].lower()
    if head.startswith("content") or head in {"type", "bottom sheet"}:
        return None  # header row / spanning title
    ar = cells[0] if len(cells) > 0 else ""
    en = cells[1] if len(cells) > 1 else ""
    kind = cells[2] if len(cells) > 2 else ""
    return (ar, en, kind)


class _DfScan:
    """Line-by-line scan state: finished tables, the open table id and its rows."""

    def __init__(self) -> None:
        self.tables: list[DataFieldTable] = []
        self.current_id: str | None = None
        self.rows: list[tuple[str, str, str]] = []

    def flush(self) -> None:
        if self.current_id and self.rows:
            self.tables.append(
                DataFieldTable(table_id=self.current_id, rows=tuple(self.rows))
            )

    def _open(self, raw_id: str) -> None:
        self.flush()
        self.rows = []
        self.current_id = raw_id if raw_id.startswith("DF") else None

    def feed(self, line: str) -> None:
        section = _section_id_match(line)
        if section:
            self._open(rp.WS_RE.sub("", section.group("id")).upper())
            return
        if self.current_id is None or len(self.rows) >= _MAX_ROWS_PER_TABLE:
            return
        row = _data_field_row(_split_row(line))
        if row is not None:
            self.rows.append(row)


# A table is attributed to the most recent ``**DFnn**`` marker above it. The
# SAME declared id may appear twice -- that is a ticket defect, reported by
# source_ambiguity_issues, not silently merged here.
def parse_data_field_tables(description: str) -> list[DataFieldTable]:
    """Every DF table in the description, in order of appearance."""
    scan = _DfScan()
    try:
        for line in _join_wrapped_rows(description):
            if len(scan.tables) >= _MAX_TABLES:
                logger.info(
                    "parse_data_field_tables hit the %d-table cap - truncating",
                    _MAX_TABLES,
                )
                break
            scan.feed(line)
        scan.flush()
    except Exception:
        logger.exception("parse_data_field_tables failed - returning what was parsed")
    return scan.tables


def _table_key(seen: dict[str, int], table_id: str) -> str:
    """Unit-id prefix for ``table_id``: the id, ``#n``-suffixed from the 2nd use."""
    seen[table_id] = seen.get(table_id, 0) + 1
    if seen[table_id] == 1:
        return table_id
    # Numeric, not chr(ord('a') + n): past the 26th occurrence that
    # produced ids like "DF02|-1", and a pipe inside a unit id
    # corrupts any pipe-delimited rendering of the traceability matrix.
    return f"{table_id}#{seen[table_id]}"


def _df_text(en: str, ar: str, kind_label: str) -> str:
    """Unit text of a data-field row: ``en / ar`` plus the ``[kind]`` label."""
    text = " / ".join(p for p in (en, ar) if p)
    return f"{text} [{kind_label}]" if kind_label else text


# A ticket may reuse a table id for two different screens (TICKET-5645
# labels both the confirmation dialog and the success screen DF02). That
# is reported by source_ambiguity_issues; here the SECOND occurrence is
# suffixed so every unit id stays unique and therefore citable.
def _add_data_field_units(
    description: str, add: Callable[[str, str, str | None], None]
) -> None:
    """Feed every data-field table row to ``add`` with its ``DFnn-n`` id."""
    seen_tables: dict[str, int] = {}
    for table in parse_data_field_tables(description):
        table_key = _table_key(seen_tables, table.table_id)
        for index, (ar, en, kind_label) in enumerate(table.rows, 1):
            add("data_field", _df_text(en, ar, kind_label), f"{table_key}-{index}")

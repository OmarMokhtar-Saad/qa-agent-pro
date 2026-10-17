"""Media records produced before their case record exists, held until it does."""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_PENDING: dict[tuple[str, str], list] = {}


def hold(run_id: str, tc_id: str, records: list) -> None:
    """Keep *records* for a case whose record is not written yet."""
    items = [item for item in list(records or []) if isinstance(item, dict)]
    if items:
        with _LOCK:
            _PENDING.setdefault((run_id, tc_id), []).extend(items)


def take(run_id: str, tc_id: str) -> list:
    """Remove and return every record held for the case."""
    with _LOCK:
        return _PENDING.pop((run_id, tc_id), [])

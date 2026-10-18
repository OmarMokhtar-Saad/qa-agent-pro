"""Media records produced before their case record exists, held until it does."""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_PENDING: dict[tuple[str, str], list] = {}
#: Most cases held at once; the oldest is dropped so a case that never gets a
#: record cannot grow this map for the life of the process.
MAX_HELD_CASES = 64


def _trim() -> None:
    """Drop the oldest cases beyond the cap; the caller holds the lock."""
    while len(_PENDING) > MAX_HELD_CASES:
        del _PENDING[next(iter(_PENDING))]


def hold(run_id: str, tc_id: str, records: list) -> None:
    """Keep *records* for a case whose record is not written yet."""
    items = [item for item in list(records or []) if isinstance(item, dict)]
    if items:
        with _LOCK:
            _PENDING.setdefault((run_id, tc_id), []).extend(items)
            _trim()


def take(run_id: str, tc_id: str) -> list:
    """Remove and return every record held for the case."""
    with _LOCK:
        return _PENDING.pop((run_id, tc_id), [])


def put_back(run_id: str, tc_id: str, records: list) -> None:
    """Return *records* taken by :func:`take` whose case write failed, first in line."""
    items = [item for item in list(records or []) if isinstance(item, dict)]
    if items:
        with _LOCK:
            _PENDING[(run_id, tc_id)] = [*items, *_PENDING.get((run_id, tc_id), [])]
            _trim()


def drop_run(run_id: str) -> None:
    """Forget everything held for a run that has ended."""
    with _LOCK:
        for key in [k for k in _PENDING if k[0] == run_id]:
            del _PENDING[key]

"""Which runs turned auto-replay off (``flow=none``), held in process memory only."""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_OFF: set = set()
_MAX_RUNS = 200


def optout(run_id: object) -> None:
    """Stop auto-replaying saved flows for *run_id*."""
    with _LOCK:
        if len(_OFF) >= _MAX_RUNS:
            _OFF.clear()
        _OFF.add(str(run_id or ""))


def opted_out(run_id: object) -> bool:
    with _LOCK:
        return str(run_id or "") in _OFF


def clear() -> None:
    with _LOCK:
        _OFF.clear()

"""Which runs turned auto-replay off (``flow=none``), held in process memory only."""

from __future__ import annotations

import re
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


#: The user's own words asking for a run without saved flows / replay.
_ASKED = re.compile(
    r"\b(?:no|without|skip|disable|ignore|avoid|don'?t use|do not use)\s+"
    r"(?:the\s+|any\s+|all\s+)?(?:saved\s+|learned\s+|known\s+)?(?:flows?|replay)\b"
    r"|\bfrom scratch\b",
    re.IGNORECASE,
)


def user_declined(goal: object) -> bool:
    """True when the run's goal itself asks for no saved flow."""
    return bool(_ASKED.search(str(goal or "")))


def honoured(run_id: object, goal: object) -> bool:
    """``flow=none`` counts only when the user explicitly asked for no flow."""
    return opted_out(run_id) and user_declined(goal)


def clear() -> None:
    with _LOCK:
        _OFF.clear()

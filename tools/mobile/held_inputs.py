"""Tester-supplied values held for ONE run, in process memory only.

Fix round 3, item 1. On the Air run the tester gave ``login_national_id`` and
``login_password`` once and was asked for them again on five of eight turns:
a value lived on the executor ``Context`` for ONE call
(``Context.tester_inputs``), the next turn started with none, and
``ask_tester`` paused for it again.

WHY A MODULE OF ITS OWN. ``session.py`` promises that no run state lives in
memory (its header, pinned by ``tests/mobile/test_mobile_session.py``), and a
secret must never be written to the run folder, the checkpoint, the report or
the audit log. So the value may live in exactly one place: this process's
memory, for a bounded time, keyed by run id.

What that costs, stated: a server restart forgets the values and the tester is
asked once more. That is the safe direction.

Bounded three ways -- :data:`MAX_HELD_RUNS`, :data:`MAX_HELD_FIELDS` and
:data:`HOLD_TTL_S` -- and ``session.submit`` calls :func:`forget` once the run
has reached its report. Nothing here logs a value; only field names leave.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

#: Runs whose values are held at once. The oldest is dropped first.
MAX_HELD_RUNS = 4
#: Fields held per run. A login form is an id, a password and an OTP.
MAX_HELD_FIELDS = 8
#: Seconds of disuse before a run's values are dropped. SLIDING: every
#: ``remember`` and ``recall`` restarts the clock, so a long run in use keeps them.
HOLD_TTL_S = 1800

_LOCK = threading.Lock()
_HELD: dict[str, tuple[float, dict[str, str]]] = {}


def _clock(now: float | None) -> float:
    return time.monotonic() if now is None else float(now)


def _prune(now: float) -> None:
    for run_id in [r for r, (seen, _) in _HELD.items() if now - seen > HOLD_TTL_S]:
        _HELD.pop(run_id, None)


def remember(run_id: str, values: object, *, now: float | None = None) -> None:
    """Hold *values* (``field -> value``) for *run_id*. Never raises."""
    try:
        key = str(run_id or "")
        if not key or not isinstance(values, dict) or not values:
            return
        stamp = _clock(now)
        with _LOCK:
            _prune(stamp)
            held = dict((_HELD.get(key) or (stamp, {}))[1])
            for name, value in values.items():
                field = str(name or "").strip()[:80]
                if not field or value is None:
                    continue
                if field not in held and len(held) >= MAX_HELD_FIELDS:
                    continue
                held[field] = str(value)
            _HELD[key] = (stamp, held)
            while len(_HELD) > MAX_HELD_RUNS:
                oldest = min(_HELD, key=lambda r: _HELD[r][0])
                _HELD.pop(oldest, None)
    except Exception:  # pragma: no cover - defensive
        # No exc_info: a traceback's locals could carry the value.
        logger.debug("held_inputs.remember failed")


def recall(run_id: str, *, now: float | None = None) -> dict[str, str]:
    """A COPY of what is held for *run_id*, ``{}`` when nothing is. Never raises."""
    try:
        stamp = _clock(now)
        with _LOCK:
            _prune(stamp)
            key = str(run_id or "")
            entry = _HELD.get(key)
            if entry:
                # Sliding: a run still in use keeps its values.
                _HELD[key] = (stamp, entry[1])
            return dict(entry[1]) if entry else {}
    except Exception:  # pragma: no cover - defensive
        return {}


def held_fields(run_id: str, *, now: float | None = None) -> list[str]:
    """The NAMES held for *run_id*, sorted. Never a value."""
    return sorted(recall(run_id, now=now))


def forget(run_id: str) -> None:
    """Drop everything held for *run_id*. Never raises."""
    try:
        with _LOCK:
            _HELD.pop(str(run_id or ""), None)
    except Exception:  # pragma: no cover - defensive
        logger.debug("held_inputs.forget failed")


def clear() -> None:
    """Drop everything. For tests."""
    with _LOCK:
        _HELD.clear()

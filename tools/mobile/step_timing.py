"""Where one ``qa_submit_mobile_step`` call spent its time, phase by phase.

The Air run that motivated this (mrun-20260928-160257-cbcdc3) took 104 s for a
five-action script and nothing said where: the audit row carries one
``duration_ms`` for the whole call. This module is the breakdown.

A CONTEXTVAR, for the reason ``_MOBILE_IMAGE_SPECS`` in ``mcp_handlers`` is
one: the handler has many return points and the timed work happens several
calls down (``executor._dump``, ``executor._sleep``, ``media.finish_step``), so
threading a timer through every signature would touch dozens of lines to
carry one number. ``begin()`` arms it for the current task; every ``timed``
call with nothing armed is a plain ``await`` -- the explore lane, the run
start and every test that never calls ``begin()`` pay nothing and record
nothing.

PHASES NEST. ``replay`` is the whole ``session.submit`` call, and ``ui_dump``,
``wait``, ``ime_check`` and ``evidence`` are timed INSIDE it, so the phases do
not add up to the total and are not meant to: ``replay`` answers "how long did
the script take", the inner ones answer "on what".

A phase whose awaitable raises is not recorded; the exception propagates
unchanged. Timing is a report, never a behaviour.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any, Awaitable

#: Distinct phase names one call may record. The instrumented sites name six;
#: a name past this is dropped rather than grown into an unbounded dict that
#: ends up in a reply. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
MAX_TIMED_PHASES = 20

_PHASES: ContextVar = ContextVar("_STEP_PHASES", default=None)


def begin() -> None:
    """Arm a fresh breakdown for the current task (and its awaited callees)."""
    _PHASES.set({"start": time.monotonic(), "phases": {}})


def _record(name: str, seconds: float) -> None:
    state = _PHASES.get()
    if state is None:
        return
    phases = state["phases"]
    if name not in phases and len(phases) >= MAX_TIMED_PHASES:
        return
    total, count = phases.get(name, (0.0, 0))
    phases[name] = (total + seconds, count + 1)


async def timed(name: str, awaitable: Awaitable[Any]) -> Any:
    """Await *awaitable*, adding its wall time to phase *name* when armed."""
    if _PHASES.get() is None:
        return await awaitable
    start = time.monotonic()
    result = await awaitable
    _record(name, time.monotonic() - start)
    return result


def mark(name: str) -> None:
    """Count one EVENT under *name* -- a dump reused, skipped or cut.

    Not a phase: nothing was awaited, so there is no time to add. It shares
    :data:`MAX_TIMED_PHASES` with the phases because both end up in the one
    line :func:`line` renders. Unarmed, it records nothing.
    """
    state = _PHASES.get()
    if state is None:
        return
    marks = state.setdefault("marks", {})
    if name not in marks and len(marks) >= MAX_TIMED_PHASES:
        return
    marks[name] = marks.get(name, 0) + 1


def summary() -> dict:
    """``{"total_ms": int, "phases": {name: {"ms": int, "n": int}}}`` or ``{}``."""
    state = _PHASES.get()
    if state is None:
        return {}
    return {
        "total_ms": int((time.monotonic() - state["start"]) * 1000),
        "phases": {
            name: {"ms": int(total * 1000), "n": count}
            for name, (total, count) in state["phases"].items()
        },
    }


def line() -> str:
    """One ``⏱`` line for the reply and the log, or ``""`` when not armed."""
    data = summary()
    if not data:
        return ""
    parts = [
        f"{name} {info['ms']:,} ms" + (f" ×{info['n']}" if info["n"] > 1 else "")
        for name, info in data["phases"].items()
    ]
    marks = (_PHASES.get() or {}).get("marks") or {}
    parts += [f"{name} ×{count}" for name, count in marks.items()]
    return f"⏱ step {data['total_ms']:,} ms" + (
        " — " + " · ".join(parts) if parts else ""
    )

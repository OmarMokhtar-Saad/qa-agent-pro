"""Replay waits: do not re-read a screen the device says has not moved.

A watched poll used to dump back to back (87-99 dumps in one ~10 s wait). The
qa-ime event clock stamps every dump with a ``generation`` (every event counts;
the event ``count`` on 1.2.1). A read whose token equals the previous read's
means no event arrived in between, so the next read is held back with a
backoff that grows while the token stays put and resets the moment it moves.
Every pause is cut to the wait's deadline, so it is a ceiling, never a delay.
No clock (uiautomator fallback, old qa-ime) -> no gate: the dump is the poll.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from tools.mobile import a11y_events
from tools.mobile.replay.context import Context
from tools.mobile.replay.context import _sleep as pause

#: First hold after an unchanged read (s); doubles per further unchanged read.
HOLD_START_S = 0.05
#: Longest single hold (s): an event is noticed within this of arriving.
HOLD_LONGEST_S = 0.4


@dataclass
class Gate:
    """The token of the previous read and how many unchanged reads followed it."""

    token: int | None = None
    idle: int = 0
    #: A poll with no dump in it (a bare focus probe): no clock still means "no news".
    probe_only: bool = False
    started: bool = False


def token_of(serial: str) -> int | None:
    """The generation of *serial*'s latest dump, or ``None`` without one.

    Only the every-event ``generation`` proves quiet: the window/content
    ``count`` (qa-ime 1.2.1) misses events, so it never holds a read back.
    """
    clock = a11y_events.latest(serial)
    return None if clock is None else clock.generation


def _hold_s(idle: int) -> float:
    return min(HOLD_LONGEST_S, HOLD_START_S * 2 ** max(0, idle - 1))


async def hold(ctx: Context, gate: Gate, deadline: float) -> None:
    """Pause before the next read while the event token has not moved."""
    token = token_of(ctx.serial)
    blind = token is None and not gate.probe_only
    if not gate.started or token != gate.token or blind:
        gate.started, gate.token, gate.idle = True, token, 0
        return
    gate.idle += 1
    left = deadline - time.monotonic()
    await pause(min(_hold_s(gate.idle), max(0.0, left)))

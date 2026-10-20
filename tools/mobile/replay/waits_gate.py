"""Replay waits: do not re-read a screen the device says has not moved.

A watched poll used to dump back to back (87-99 dumps in one ~10 s wait). The
qa-ime event clock stamps every dump with a ``generation`` (every event counts;
the event ``count`` on 1.2.1). A read whose token equals the previous read's
means no event arrived in between, so the next read is held back until the
NEXT generation (:mod:`tools.mobile.a11y_next`), and the hold ends the moment
it moves. Every hold is cut to the wait's deadline, so it is a ceiling, never a
delay. No clock (uiautomator fallback, old qa-ime) -> no gate, for every
caller: the dump is the poll.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from tools.mobile import a11y_events, a11y_next
from tools.mobile.replay.context import Context
from tools.mobile.replay.context import _sleep as pause

#: Pause (s) when the generation cannot be watched after all (a failed a11y read).
HOLD_FALLBACK_S = 0.05


@dataclass
class Gate:
    """The token of the previous read and how many unchanged reads followed it."""

    token: int | None = None
    idle: int = 0
    started: bool = False


def token_of(serial: str) -> int | None:
    """The generation of *serial*'s latest dump, or ``None`` without one.

    Only the every-event ``generation`` proves quiet: the window/content
    ``count`` (qa-ime 1.2.1) misses events, so it never holds a read back.
    """
    clock = a11y_events.latest(serial)
    return None if clock is None else clock.generation


async def hold(ctx: Context, gate: Gate, deadline: float) -> None:
    """Wait for news before the next read while the event token has not moved."""
    token = token_of(ctx.serial)
    if not gate.started or token is None or token != gate.token:
        gate.started, gate.token, gate.idle = True, token, 0
        return
    gate.idle += 1
    if await a11y_next.next_generation(ctx.serial, token, deadline):
        return
    left = deadline - time.monotonic()
    await pause(min(HOLD_FALLBACK_S, max(0.0, left)))

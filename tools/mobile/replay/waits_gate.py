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
from typing import Any

from tools.mobile import a11y_dump, a11y_events
from tools.mobile.replay.context import Context
from tools.mobile.replay.context import _sleep as pause
from tools.mobile.replay.dump import _observed as observed_screen

#: Longest one hold may re-read the cheap a11y dump for a new generation (s). A clock
#: that stops moving (audit-1147 D12: 182 adb calls, the "Open" button never read) must
#: hand the next read to the real poll. Hold reads do not count toward the deaf-service
#: skip budget (``a11y_clock.GENERATION_SKIPS`` = 6 real reads, then a uiautomator
#: confirm), so the cap is kept small: the 7th real read lands about 3.5 s in, inside a
#: 5 s wait. Every hold read is checked against the wait's condition anyway.
HOLD_CAP_S = 0.5

#: Pause (s) when the generation cannot be watched after all (a failed a11y read).
HOLD_FALLBACK_S = 0.05


@dataclass
class Gate:
    """The token of the previous read and how many unchanged reads followed it."""

    token: int | None = None
    idle: int = 0
    started: bool = False
    met: Any = None  # ``met(previous, current)`` of the wait, to check hold reads


def token_of(serial: str) -> int | None:
    """The generation of *serial*'s latest dump, or ``None`` without one.

    Only the every-event ``generation`` proves quiet: the window/content
    ``count`` (qa-ime 1.2.1) misses events, so it never holds a read back.
    """
    clock = a11y_events.latest(serial)
    return None if clock is None else clock.generation


# Each checked hold read goes through ``observed_screen``, so it feeds ``screen_seen``'s
# ring (``wait_guard.SEEN`` reads) like a real poll: a long hold can fill that
# window sooner than the real polls alone would.
async def _hold_reads(ctx: Context, gate: Gate, until: float) -> tuple:
    """Re-read the cheap a11y dump until the token moves or *until*.

    ``(watched, screen)``: *watched* is False when a read failed or time ran out;
    *screen* is a hold read that already satisfies the wait, else ``None``.
    """
    while time.monotonic() < until:
        raw = await a11y_dump.fetch_dump(ctx.serial)
        if raw.get("error"):
            return False, None
        if token_of(ctx.serial) != gate.token:
            return True, None
        if gate.met is None:
            continue
        screen = (await observed_screen(ctx, raw)).get("content")
        if gate.met(None, screen):
            return True, screen
    return False, None  # the cap or the deadline came first: a short pause


async def hold(ctx: Context, gate: Gate, deadline: float) -> object:
    """Wait for news before the next read while the event token has not moved.

    Returns a hold read that already meets the wait (the generation did not
    move, the screen did), else ``None``.
    """
    token = token_of(ctx.serial)
    if not gate.started or token is None or token != gate.token:
        gate.started, gate.token, gate.idle = True, token, 0
        return None
    gate.idle += 1
    watched, screen = await _hold_reads(
        ctx, gate, min(deadline, time.monotonic() + HOLD_CAP_S)
    )
    if watched:
        return screen
    left = deadline - time.monotonic()
    await pause(min(HOLD_FALLBACK_S, max(0.0, left)))
    return None

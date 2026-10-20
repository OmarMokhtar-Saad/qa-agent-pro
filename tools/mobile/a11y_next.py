"""Wait for the a11y service's NEXT event generation, instead of sleeping a fixed gap.

The qa-ime clock (:mod:`tools.mobile.a11y_events`) only reaches the host inside a
dump reply, so "block on the clock" means re-reading the cheap a11y dump (about
100 ms each, which is the pacing) until the every-event generation differs from
the one the caller holds, or the deadline passes. No a11y clock, or an a11y read
that fails: the caller gets a short backoff pause instead, never a long one.
"""

from __future__ import annotations

import asyncio
import time

from tools.mobile import a11y_dump, a11y_events

#: Pause (s) when the generation cannot be watched (no qa-ime clock, a failed read).
NO_CLOCK_GAP_S = 0.25


def generation_of(serial: str) -> int | None:
    """The every-event generation of *serial*'s latest a11y dump, or ``None``."""
    clock = a11y_events.latest(serial)
    return None if clock is None else clock.generation


async def next_generation(serial: str, held: int, deadline: float) -> bool:
    """True when the generation moved off *held*; False at *deadline* or on a failed read."""
    while time.monotonic() < deadline:
        if (await a11y_dump.fetch_dump(serial)).get("error"):
            return False
        now = generation_of(serial)
        if now is None:
            return False
        if now != held:
            return True
    return False


async def pause_for_news(serial: str, deadline: float) -> None:
    """Return when the screen may have news: a new generation, else a short pause.

    Always bounded by *deadline* (a monotonic time).
    """
    held = generation_of(serial)
    if held is not None and await next_generation(serial, held, deadline):
        return
    await asyncio.sleep(min(NO_CLOCK_GAP_S, max(0.0, deadline - time.monotonic())))

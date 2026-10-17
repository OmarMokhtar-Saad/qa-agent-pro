"""Replay waits: the a11y-event quiet wait that runs before a watched poll.

qa-ime 1.2.1+ stamps every fast dump with the device's event clock
(``tools.mobile.a11y_events``). When the last dump carried one, a wait first
lets the screen go quiet: dump until a dump saw MORE events than the one taken
before the wait AND its last event is at least ``QUIET_MS`` old (both read from
the device's own clock, never the host's). It is bounded by ``QUIET_CAP_S``
and by the wait's own deadline. Quiet does not prove pixels settled (video,
canvas), so the caller keeps its dump-diff check. A screen that sent no event
at all is at rest once the device clock moved ``REST_MS`` past the baseline
dump with the count unchanged, so a wait on a still screen never pays the cap.
No clock on the last dump (qa-ime 1.2.0) -> no-op.
"""

from __future__ import annotations

import time

from tools.mobile import a11y_events
from tools.mobile.replay.context import Context, _sleep
from tools.mobile.replay.dump import _dump

#: How long the device must have sent no window/content event (ms).
QUIET_MS = 200
#: The longest the quiet wait may take before the dump-diff path runs (s).
QUIET_CAP_S = 2.0
#: Device time with no new event after which a still screen is at rest (ms).
REST_MS = 500


def _is_quiet(clock: a11y_events.EventClock, before: a11y_events.EventClock) -> bool:
    """True when *clock* saw new events and is quiet long enough, or saw none
    while the device clock moved ``REST_MS`` past *before* (a still screen)."""
    if clock.count > before.count:
        return clock.quiet_ms() >= QUIET_MS
    return clock.count == before.count and clock.now_ms - before.now_ms >= REST_MS


def _rest_s(clock: a11y_events.EventClock) -> float:
    """Seconds to wait before the next dump: the rest of the quiet window."""
    return max(0.05, (QUIET_MS - clock.quiet_ms()) / 1000.0)


async def settled(ctx: Context, screen: object, deadline: float) -> object:
    """*screen*, or the newer one a quiet dump read; a dump error keeps *screen*."""
    before = a11y_events.latest(ctx.serial)
    if before is None:
        return screen
    stop = min(deadline, time.monotonic() + QUIET_CAP_S)
    while time.monotonic() < stop:
        dumped = await _dump(ctx, deadline=stop)
        clock = a11y_events.latest(ctx.serial)
        if dumped.get("error") or clock is None:
            return screen
        screen = dumped.get("content") or screen
        if _is_quiet(clock, before):
            return screen
        await _sleep(min(_rest_s(clock), max(0.0, stop - time.monotonic())))
    return screen

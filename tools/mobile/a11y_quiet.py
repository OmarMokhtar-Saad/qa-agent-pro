"""Let the screen go quiet before a slow uiautomator read.

``uiautomator dump`` itself waits for 1 s with no accessibility event (capped at
10 s, and it writes nothing on timeout), so on an animating or launching screen
one read costs 10 s or more. Fast a11y dumps are cheap and carry the device's
event clock, so the caller reads those until the last event is ``QUIET_MS`` old,
for at most ``QUIET_CAP_S``; past the cap the slow read runs anyway. No clock
(qa-ime 1.2.0 and older) or a dump error: no wait.
"""

from __future__ import annotations

import time

from tools.mobile import a11y_dump, a11y_events
from tools.mobile.replay.context import _sleep as sleep

#: What uiautomator needs to see: no event for this long (ms).
QUIET_MS = 1000
#: The longest this wait may take (s).
QUIET_CAP_S = 3.0
#: Pause between fast dumps (s).
POLL_S = 0.1


async def wait_quiet(serial: str) -> None:
    """Fast-dump until the event clock shows ``QUIET_MS`` of quiet, or the cap."""
    stop = time.monotonic() + QUIET_CAP_S
    while time.monotonic() < stop:
        reply = await a11y_dump.fetch_dump(serial)
        clock = a11y_events.latest(serial)
        if reply.get("error") or clock is None or clock.quiet_ms() >= QUIET_MS:
            return
        await sleep(min(POLL_S, max(0.0, stop - time.monotonic())))

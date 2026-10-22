"""Replay waits: when a watched wait for text may give up before its budget.

Never from a fixed delay, only on proof: a wait for text to APPEAR ends early
(not found) when the content stayed equal for ``EARLY_EXIT_MS`` AND
``REST_EXIT_READS`` fresh dumps in a row are equal: nothing is changing, so the
text will not come. The time is the device event clock when there is one; with
no clock (qa-ime before 1.2.1, or the accessibility service paused so every
dump is a uiautomator dump) it is host time since the content last changed.
"""

from __future__ import annotations

import time

from tools.mobile import a11y_events

#: Host clock for the no-event-clock path; tests patch this alias.
monotonic = time.monotonic

#: Device time with no accessibility event after which a still screen is at rest (ms).
REST_EXIT_MS = 5000
#: Equal dumps in a row, besides the silent clock, that prove the screen at rest.
REST_EXIT_READS = 3
#: Event-clock silence after which a wait for absent text gives up early (ms).
EARLY_EXIT_MS = 10000


def at_rest(serial: str, quiet: int) -> bool:
    """True when *quiet* equal dumps in a row and a silent event clock say rest."""
    clock = a11y_events.latest(serial)
    return (
        clock is not None
        and quiet >= REST_EXIT_READS
        and clock.quiet_ms() >= REST_EXIT_MS
    )


class Rest:
    """Equal dumps in a row, and the device time since the content last changed.

    Events can trickle (a clock, a cursor) while the content stays equal; the
    event clock then never goes silent, so ``quiet_exit`` alone never fires (G08).
    ``still_ms`` is the device time the content has been equal for; with no event
    clock ``still_host_ms`` is the host time instead (D-06).
    """

    def __init__(self) -> None:
        self.quiet = 0
        self._since: int | None = None
        self._clock_ms: int | None = None
        self._host: float | None = None

    def read(self, serial: str, same: bool) -> None:
        """Take one more dump, *same* as the one before it or not."""
        self.quiet = (self.quiet + 1) * same
        clock = a11y_events.latest(serial)
        now = clock.now_ms if clock is not None else None
        fresh = (
            now is not None and now != self._clock_ms
        )  # a stale clock proves nothing
        self._clock_ms = now if now is not None else self._clock_ms
        if not same:
            self._since = now if fresh else None
        elif fresh and self._since is None:
            self._since = now
        self._stamp(same)

    def _stamp(self, same: bool) -> None:
        """Host time of the first read, and of every read whose content changed."""
        if not same or self._host is None:
            self._host = monotonic()

    def still_host_ms(self) -> int:
        """Host ms the content has stayed equal for (0 before the equal reads)."""
        if self._host is None or self.quiet < REST_EXIT_READS:
            return 0
        return max(0, int((monotonic() - self._host) * 1000))

    def still_ms(self, serial: str) -> int:
        """Device ms the content has stayed equal for (0 without an event clock)."""
        clock = a11y_events.latest(serial)
        if clock is None or self._since is None or self.quiet < REST_EXIT_READS:
            return 0
        return max(0, clock.now_ms - self._since)


def still_exit(serial: str, rest: Rest) -> bool:
    """True when the content stayed equal for ``EARLY_EXIT_MS`` (device time, else host)."""
    if a11y_events.latest(serial) is None:
        return rest.still_host_ms() >= EARLY_EXIT_MS
    return rest.still_ms(serial) >= EARLY_EXIT_MS


def quiet_exit(serial: str, quiet: int) -> bool:
    """True when the screen is at rest AND the device event clock is silent ``EARLY_EXIT_MS``."""
    clock = a11y_events.latest(serial)
    return (
        clock is not None
        and at_rest(serial, quiet)
        and clock.quiet_ms() >= EARLY_EXIT_MS
    )

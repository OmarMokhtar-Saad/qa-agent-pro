"""Replay waits: when a watched wait for text may give up before its budget.

Never from a fixed delay, only on event-clock proof: a wait for text to APPEAR
ends early (not found) when the device's event clock has been silent for
``EARLY_EXIT_MS`` AND ``REST_EXIT_READS`` dumps in a row are equal: nothing is
changing, so the text will not come. No event clock (qa-ime before 1.2.1) ->
the full budget is waited.
"""

from __future__ import annotations

from tools.mobile import a11y_events

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
    ``still_ms`` is the device time the content has been equal for.
    """

    def __init__(self) -> None:
        self.quiet = 0
        self._since: int | None = None
        self._clock_ms: int | None = None

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

    def still_ms(self, serial: str) -> int:
        """Device ms the content has stayed equal for (0 without an event clock)."""
        clock = a11y_events.latest(serial)
        if clock is None or self._since is None or self.quiet < REST_EXIT_READS:
            return 0
        return max(0, clock.now_ms - self._since)


def still_exit(serial: str, rest: Rest) -> bool:
    """True when the content stayed equal for ``EARLY_EXIT_MS`` of device time."""
    return rest.still_ms(serial) >= EARLY_EXIT_MS


def quiet_exit(serial: str, quiet: int) -> bool:
    """True when the screen is at rest AND silent for ``EARLY_EXIT_MS``."""
    clock = a11y_events.latest(serial)
    return (
        clock is not None
        and at_rest(serial, quiet)
        and clock.quiet_ms() >= EARLY_EXIT_MS
    )

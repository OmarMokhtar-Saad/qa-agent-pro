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


def quiet_exit(serial: str, quiet: int) -> bool:
    """True when the screen is at rest AND silent for ``EARLY_EXIT_MS``."""
    clock = a11y_events.latest(serial)
    return at_rest(serial, quiet) and clock.quiet_ms() >= EARLY_EXIT_MS

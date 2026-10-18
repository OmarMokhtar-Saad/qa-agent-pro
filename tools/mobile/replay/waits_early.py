"""Replay waits: when a watched poll stops paying for reads that cannot change the answer.

Two cuts, both from what the polls already saw, never from a fixed delay and
NEVER by ending a wait before its budget:

* P3, a wait on a screen that is provably at rest (the device's own event clock
  silent for ``REST_EXIT_MS`` AND the last ``REST_EXIT_READS`` dumps equal)
  backs its poll off to ``REST_POLL_S``: fewer dumps, same deadline. A slow
  load (a spinner or a network call sends no event) is still waited for in full;
  the first event or changed dump resets the pace. No event clock (qa-ime before
  1.2.1) -> never backs off.
* P4, the cheap probe gate is dropped for the rest of the wait once it keeps
  saying "moved" while the dumps say "same" (``GATE_NOISE_READS`` in a row: a
  spinner or video moves pixels, not the tree), or once the probe cannot be
  read at all: every further poll would pay probe + dump for one dump's answer.
"""

from __future__ import annotations

from tools.mobile import a11y_events

#: Device time with no accessibility event after which a still screen is at rest (ms).
REST_EXIT_MS = 5000
#: Equal dumps in a row, besides the silent clock, that prove the screen at rest.
REST_EXIT_READS = 3
#: Pause between dumps while the screen is at rest (s); the wait still runs to its deadline.
REST_POLL_S = 2.0
#: Equal dumps in a row after "moved" probes that show the probe is only noise.
GATE_NOISE_READS = 2


def at_rest(serial: str, quiet: int) -> bool:
    """True when *quiet* equal dumps in a row and a silent event clock say rest."""
    clock = a11y_events.latest(serial)
    return (
        clock is not None
        and quiet >= REST_EXIT_READS
        and clock.quiet_ms() >= REST_EXIT_MS
    )


def rest_pause(serial: str, quiet: int, pause: float) -> float:
    """*pause*, stretched to ``REST_POLL_S`` while the screen is at rest."""
    return max(pause, REST_POLL_S) if at_rest(serial, quiet) else pause


def gate_is_noise(quiet: int) -> bool:
    """True when the probe said "moved" but *quiet* dumps in a row stayed equal."""
    return quiet >= GATE_NOISE_READS

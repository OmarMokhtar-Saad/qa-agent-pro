"""Replay waits: when a watched poll stops paying for reads that cannot change the answer.

Three cuts, all from what the polls already saw, never from a fixed delay.
P3 and P4 never end a wait before its budget; P5 may, only on event-clock proof:

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
* P5, a wait for text to APPEAR ends early (not found) when the event clock has
  been silent for ``EARLY_EXIT_MS`` AND ``REST_EXIT_READS`` dumps are equal:
  nothing is changing, so the text will not come. No event clock -> the full
  budget is waited.
"""

from __future__ import annotations

import time

from tools.mobile import a11y_events

#: Device time with no accessibility event after which a still screen is at rest (ms).
REST_EXIT_MS = 5000
#: Equal dumps in a row, besides the silent clock, that prove the screen at rest.
REST_EXIT_READS = 3
#: Event-clock silence after which a wait for absent text gives up early (ms).
EARLY_EXIT_MS = 10000
#: Pause between dumps while the screen is at rest (s); the wait still runs to its deadline.
REST_POLL_S = 2.0
#: Equal dumps in a row after "moved" probes that show the probe is only noise.
GATE_NOISE_READS = 2
#: Quiet probe time after which a fast-reader wait cross-checks with one real dump (s).
CHANGED_CHECK_S = 4.0


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


def rest_pause(serial: str, quiet: int, pause: float) -> float:
    """*pause*, stretched to ``REST_POLL_S`` while the screen is at rest."""
    return max(pause, REST_POLL_S) if at_rest(serial, quiet) else pause


def gate_is_noise(quiet: int) -> bool:
    """True when the probe said "moved" but *quiet* dumps in a row stayed equal."""
    return quiet >= GATE_NOISE_READS


def check_horizon(serial: str, until: float) -> float:
    """How far a probe-only wait may look before taking a real dump.

    With the fast a11y reader (an event clock) dumps are cheap, so a quiet
    probe is cross-checked every ``CHANGED_CHECK_S``: a change the pixels
    cannot show ends the wait then, not at its cap. Without it: *until*.
    """
    if a11y_events.latest(serial) is None:
        return until
    return min(until, time.monotonic() + CHANGED_CHECK_S)

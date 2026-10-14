"""The beating thread for ONE run, and the pace it beats at.

Split out of ``heartbeat``; that module re-exports ``INTERVAL_S``, ``Pace`` and
``_Writer``. The three decisions live in ``heartbeat_beat`` and are reached
through the module object so a patch of ``heartbeat_beat.beat_once`` bites.
"""

from __future__ import annotations

import threading
from typing import NamedTuple

from tools.mobile import heartbeat_beat as beat
from tools.mobile import run_lease_release, run_store

#: One quarter of ``run_store.LEASE_STALE_S``, so THREE consecutive missed beats
#: are needed before another chat may take the run over. Derived rather than
#: literal: a future change to the staleness window must not silently turn this
#: into a writer that beats slower than the lease goes stale.
INTERVAL_S = max(1.0, float(run_store.LEASE_STALE_S) / 4.0)


class Pace(NamedTuple):
    """How often a writer beats, and how many beats bound it (0 = unbounded)."""

    interval_s: float = INTERVAL_S
    max_beats: int = 0


class _Writer:
    """One run's beating thread. Private: nothing outside this module holds one."""

    def __init__(self, run_id: str, session_token: str, pace: Pace) -> None:
        self.run_id = str(run_id)
        self.session_token = str(session_token)
        self.interval_s = float(pace.interval_s)
        self.max_beats = int(pace.max_beats)
        self.beats = 0
        self.stop_reason = ""
        self._stop = threading.Event()
        self.thread = threading.Thread(
            target=self._loop, name="qa-mobile-heartbeat", daemon=True
        )

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        """Beat FIRST, then wait.

        The order is not cosmetic, and it was chosen by RUNNING it: with the
        wait first, a `start()` immediately followed by `stop()` recorded zero
        beats -- the stop event won the race -- so the writer had no observable
        effect and the test asserting one was flaky by construction. Beating
        first is also the better behaviour: starting a heartbeat refreshes the
        lease at once rather than one interval later, which is exactly what a
        chat resuming a run wants.
        """
        while True:
            if self._step():
                break
        self._stop.set()

    # THE RUN IS OVER, so stop holding its emulator.
    #
    # WHY THE BEAT ABOVE DOES NOT COVER THIS: `beat_once` answers "is
    # the lease still mine", and for a run that has reached its report
    # the answer is yes forever -- so the writer beat on and the device
    # stayed held until the process exited. Measured: a run that stopped
    # at `deadline_reached` held the emulator for 35 minutes while every
    # other chat was refused, and the refusal offered a take-over that a
    # finished run cannot accept.
    #
    # AFTER `max_beats` so a bounded test writer still stops for the
    # reason its test named, and BEFORE the wait so the device comes
    # back on the next beat rather than at process exit.
    def _step(self) -> bool:
        """One beat, stop checks, then the wait; ``True`` means stop."""
        if not beat.beat_once(self.run_id, self.session_token):
            self.stop_reason = "lease_lost"
            beat._release_device_lock(self.run_id, self.session_token)
            return True
        self.beats += 1
        if self.max_beats and self.beats >= self.max_beats:
            self.stop_reason = "max_beats"
            return True
        if beat._run_is_finished(self.run_id, self.session_token):
            self.stop_reason = "run_finished"
            beat._release_device_lock(self.run_id, self.session_token)
            run_lease_release.mark_released(self.run_id, self.session_token)
            return True
        if self._stop.wait(self.interval_s):
            self.stop_reason = self.stop_reason or "stopped"
            return True
        return False

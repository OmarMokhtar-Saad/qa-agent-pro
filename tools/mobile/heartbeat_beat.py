"""The three decisions a heartbeat writer makes about a run's lease and device.

Split out of ``heartbeat`` so the writer thread and the public start/stop
surface live apart. ``tools.mobile.heartbeat`` re-exports every name here.
"""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import locks, run_store, session_runs
from tools.mobile.session_core import STATE_REPORT

logger = logging.getLogger("tools.mobile.heartbeat")


def beat_once(run_id: str, session_token: str) -> bool:
    """Refresh one heartbeat. ``True`` while the lease is still ours.

    Private on purpose, and the loop's whole decision: the tests drive THIS to
    check the take-over behaviour without depending on thread scheduling.
    """
    with never_raise(
        logger,
        "mobile.heartbeat: could not refresh the lease for %s",
        run_id,
        level=logging.WARNING,
    ):
        status = run_store.touch_lease(run_id, session_token)
        if status.get("error"):
            return False
        body = status.get("content") or {}
        return str(body.get("state") or "") == run_store.HELD
    return False


# Give the emulator back when THIS process loses *run_id*'s lease.
#
# THE HOLDER RELEASING ITS OWN LOCK, not a reaper. The emulator lock is held
# by this process under this run's id, and losing the lease is the moment this
# process learns it is no longer the chat driving the run -- so it is the
# moment to stop holding the device. Another chat that took the run over gets
# it on its next attempt, at most one beat interval later.
#
# Bound to the two stops that mean THIS PROCESS IS DONE DRIVING: ``lease_lost``
# (another chat took the run) and ``run_finished`` (the run reached its
# report). Deliberately NOT the other two: ``max_beats`` is a test-only bound
# and releasing there would drop a live run's lock under test, and
# ``stop``/``stop_all`` have no production caller (only this package's test
# teardown), so releasing there would only surprise a test.
#
# Never raises: a lock that could not be given back is a log line, and process
# exit gives it back regardless -- the kernel owns liveness.
#
# THE DEVICE THIS RUN ACTUALLY HOLDS, asked of the fd table rather than
# named. A fixed name here would release a lock this run never took --
# silently, since `release` answers "not held" and nothing branches on
# it -- and leave the real device held until the process exited. The
# lease is still presented, so a writer whose lease was superseded is
# refused exactly as before: this changes WHICH lock is asked, never who
# is allowed to release it.
def _release_device_lock(run_id: str, session_token: str = "") -> None:
    """Give the emulator back when THIS process loses *run_id*'s lease."""
    with never_raise(
        logger,
        "mobile.heartbeat: could not release the emulator lock for %s",
        run_id,
        level=logging.WARNING,
    ):
        for name in locks.names_held_by(str(run_id)):
            locks.release(
                name, locks.Release(owner=str(run_id), lease=str(session_token or ""))
            )


# Has *run_id* reached its terminal state? ``False`` whenever unsure.
#
# THE SAME PRODUCER the tester's own status reply reads -- ``session.resolve``
# -- rather than a second derivation here. "Is this run over" answered in two
# places drifts, and the copy that drifts is the one nobody is looking at.
#
# Imported inside the call, like ``locks`` in the sibling above: this module is
# loaded when the server starts and must not drag the session module in behind
# it.
#
# FAIL-SAFE IS KEEP BEATING. A writer that cannot tell holds the device it
# already holds, which is exactly what it did before this existed; releasing on
# an unreadable answer would hand a LIVE run's emulator to another chat, and
# that is the failure the whole lease mechanism exists to prevent.
def _run_is_finished(run_id: str, session_token: str = "") -> bool:
    """Has *run_id* reached its terminal state? ``False`` whenever unsure."""
    with never_raise(
        logger,
        "mobile.heartbeat: could not read the state of %s",
        run_id,
        level=logging.WARNING,
    ):
        resolved = session_runs.resolve(str(run_id), str(session_token or ""))
        if resolved.get("error"):
            return False
        body = resolved.get("content") or {}
        return str(body.get("state") or "") == STATE_REPORT
    return False

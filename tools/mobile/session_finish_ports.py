"""Mobile session: run-end bookkeeping and the selection lock. Cut verbatim from tools/mobile/session_finish.py (w17 sessA)."""

from __future__ import annotations

from tools.mobile import case_runner, locks, run_finalize, run_store
from tools.mobile.checkpoint_clock import last_checkpoint_ts  # noqa: F401


def awaits_tester(run_id: str) -> bool:
    """True when a case of *run_id* is paused for the tester (no verdict yet)."""
    cases = (run_store.list_cases(run_id) or {}).get("content") or []
    return any(
        c.get("status") == case_runner.NEEDS_TESTER and not c.get("verdict")
        for c in cases
    )


def run_ended(run_id: str) -> bool:
    """True when the run's manifest records a stop (tester stop or idle release)."""
    read = run_store.read_manifest(run_id).get("content")
    return bool(run_finalize.stop_text(read))


def _finalizer(stop: str):
    """A ``finalize(run_id, verdict)`` port that records ``stop`` with the verdict."""

    def finalize(run_id, verdict):
        return run_finalize.finalize(run_id, verdict, stop=stop)

    return finalize


def take_select_lock(owner: str) -> dict:
    """Serialise DEVICE SELECTION, which is a question about the MACHINE.

    Selection reads ``adb devices``, may adopt a foreign emulator and may spawn
    the shared default one, and it runs BEFORE any serial exists -- so it cannot
    be keyed by a device and must not be. This is the lock this lane has always
    taken at the top of a new run; what changed is that it is given back the
    moment a device has been named, instead of covering the install, the
    preflight and the run.
    """
    return locks.acquire(locks.EMULATOR_LOCK, locks.Claim(owner=str(owner)))


def release_select_lock(owner: str) -> dict:
    """Give selection back. The caller minted the label, so it IS the holder."""
    return locks.release(
        locks.EMULATOR_LOCK, locks.Release(owner=str(owner), as_holder=True)
    )

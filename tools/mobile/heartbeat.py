"""A background writer that keeps ONE run's lease heartbeat fresh.

WHY A THREAD AND NOT ANOTHER TOOL CALL. ``run_store.touch_lease`` already
refreshes the heartbeat on every packet-producing step, which is enough while a
tester answers quickly. It is not enough while they think: ``LEASE_STALE_S`` is
120 seconds, so a five-minute pause makes a live run look abandoned and a second
chat may take it over while the first is still driving the emulator. This closes
that window without asking the tester to poll anything.

WHAT IT DELIBERATELY DOES NOT DO. It never TAKES a lease and never forces one:
it only refreshes a lease this session already holds, and it stops the moment
``touch_lease`` reports the lease is no longer held. A writer that kept beating
after a takeover would recreate the exact failure the lease exists to prevent --
two chats each believing they hold the run -- one layer down.

THE KILL-SWITCH is read in :func:`start`, the public function that starts the
writer: it writes into the tester's cache and outlives the call that started it,
which is what the flag governs.

Never raises: every public function returns ``{"error", "content"}``, except
:func:`active`, which is pure introspection over an in-memory dict and has
nothing to fail at.
"""

from __future__ import annotations

import logging
import threading

from config.settings import settings
from tools.mobile import run_store

logger = logging.getLogger(__name__)

#: One quarter of ``run_store.LEASE_STALE_S``, so THREE consecutive missed beats
#: are needed before another chat may take the run over. Derived rather than
#: literal: a future change to the staleness window must not silently turn this
#: into a writer that beats slower than the lease goes stale.
INTERVAL_S = max(1.0, float(run_store.LEASE_STALE_S) / 4.0)

FLAG_NAME = "QA_MOBILE_RUN_ENABLED"

FLAG_REFUSAL = (
    "No lease heartbeat was started: the mobile lane needs `"
    + FLAG_NAME
    + "=true` in `.env`. Nothing is running in the background."
)

NO_TOKEN = (
    "A lease heartbeat needs the session token that holds the run; without it "
    "this would refresh somebody else's lease."
)

_LOCK = threading.Lock()
_WRITERS: dict = {}


def _beat_once(run_id: str, session_token: str) -> bool:
    """Refresh one heartbeat. ``True`` while the lease is still ours.

    Private on purpose, and the loop's whole decision: the tests drive THIS to
    check the take-over behaviour without depending on thread scheduling.
    """
    try:
        status = run_store.touch_lease(run_id, session_token)
        if status.get("error"):
            return False
        body = status.get("content") or {}
        return str(body.get("state") or "") == run_store.HELD
    except Exception:
        logger.warning("mobile.heartbeat: could not refresh the lease for %s", run_id)
        return False


def _release_device_lock(run_id: str, session_token: str = "") -> None:
    """Give the emulator back when THIS process loses *run_id*'s lease.

    THE HOLDER RELEASING ITS OWN LOCK, not a reaper. The emulator lock is held
    by this process under this run's id, and losing the lease is the moment this
    process learns it is no longer the chat driving the run -- so it is the
    moment to stop holding the device. Another chat that took the run over gets
    it on its next attempt, at most one beat interval later.

    Bound to ``lease_lost`` ONLY, and that is deliberate: ``max_beats`` is a
    test-only bound and releasing there would drop a live run's lock under test,
    and ``stop``/``stop_all`` have no production caller (only this package's test
    teardown), so releasing there would only surprise a test.

    Never raises: a lock that could not be given back is a log line, and process
    exit gives it back regardless -- the kernel owns liveness.
    """
    try:
        from tools.mobile import locks

        locks.release(
            locks.EMULATOR_LOCK, owner=str(run_id), lease=str(session_token or "")
        )
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "mobile.heartbeat: could not release the emulator lock for %s", run_id
        )


class _Writer:
    """One run's beating thread. Private: nothing outside this module holds one."""

    def __init__(
        self,
        run_id: str,
        session_token: str,
        interval_s: float,
        max_beats: int,
    ) -> None:
        self.run_id = str(run_id)
        self.session_token = str(session_token)
        self.interval_s = float(interval_s)
        self.max_beats = int(max_beats)
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
            if not _beat_once(self.run_id, self.session_token):
                self.stop_reason = "lease_lost"
                _release_device_lock(self.run_id, self.session_token)
                break
            self.beats += 1
            if self.max_beats and self.beats >= self.max_beats:
                self.stop_reason = "max_beats"
                break
            if self._stop.wait(self.interval_s):
                self.stop_reason = self.stop_reason or "stopped"
                break
        self._stop.set()


def start(
    run_id: str,
    session_token: str,
    *,
    interval_s: float = INTERVAL_S,
    max_beats: int = 0,
) -> dict:
    """Start the heartbeat for *run_id*, held by *session_token*.

    ``{"error", "content": {"started", "run_id", "interval_s", "reason"}}``.
    A second call for a run that already has a live writer returns
    ``started=False, reason="already_running"`` rather than a second writer:
    two threads refreshing one lease is not twice as safe, it is twice as many
    things to stop.

    ``max_beats=0`` beats until stopped; a positive value is a BOUND, which is
    also what makes the thread terminate deterministically under test instead of
    a sleep the assertion has to outlast.
    """
    try:
        if not settings.qa_mobile_run_enabled:
            return {"error": FLAG_REFUSAL, "content": None}
        if not run_store.valid_run_id(run_id):
            return {
                "error": "Refusing " + repr(str(run_id)[:40]) + " as a run id.",
                "content": None,
            }
        token = str(session_token or "").strip()
        if not token:
            return {"error": NO_TOKEN, "content": None}
        name = str(run_id)
        with _LOCK:
            existing = _WRITERS.get(name)
            if existing is not None and existing.thread.is_alive():
                return {
                    "error": None,
                    "content": {
                        "started": False,
                        "run_id": name,
                        "interval_s": existing.interval_s,
                        "reason": "already_running",
                    },
                }
            writer = _Writer(
                name,
                token,
                max(0.01, float(interval_s or INTERVAL_S)),
                max(0, int(max_beats or 0)),
            )
            _WRITERS[name] = writer
        writer.thread.start()
        return {
            "error": None,
            "content": {
                "started": True,
                "run_id": name,
                "interval_s": writer.interval_s,
                "reason": "started",
            },
        }
    except Exception as exc:
        logger.exception("mobile.heartbeat.start failed")
        return {"error": str(exc), "content": None}


def stop(run_id: str, *, join_timeout: float = 2.0) -> dict:
    """Stop and JOIN this run's writer. ``{stopped, run_id, beats, reason}``."""
    try:
        name = str(run_id)
        with _LOCK:
            writer = _WRITERS.pop(name, None)
        if writer is None:
            return {
                "error": None,
                "content": {
                    "stopped": False,
                    "run_id": name,
                    "beats": 0,
                    "reason": "not_running",
                },
            }
        writer.stop()
        writer.thread.join(timeout=max(0.0, float(join_timeout)))
        return {
            "error": None,
            "content": {
                "stopped": True,
                "run_id": name,
                "beats": writer.beats,
                "reason": writer.stop_reason or "stopped",
            },
        }
    except Exception as exc:
        logger.exception("mobile.heartbeat.stop failed")
        return {"error": str(exc), "content": None}


def stop_all(*, join_timeout: float = 2.0) -> dict:
    """Stop every writer. Called by the test teardown and at no other time."""
    try:
        with _LOCK:
            names = sorted(_WRITERS)
        stopped: list[str] = []
        for name in names:
            result = stop(name, join_timeout=join_timeout)
            if (result.get("content") or {}).get("stopped"):
                stopped.append(name)
        return {"error": None, "content": {"stopped": stopped}}
    except Exception as exc:
        logger.exception("mobile.heartbeat.stop_all failed")
        return {"error": str(exc), "content": None}


def active() -> list[str]:
    """Run ids with a live writer, sorted. Pure introspection."""
    with _LOCK:
        return sorted(
            name for name, writer in _WRITERS.items() if writer.thread.is_alive()
        )

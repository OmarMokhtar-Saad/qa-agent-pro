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

from tools.mobile import run_store
from tools.mobile.heartbeat_beat import (  # noqa: F401
    _release_device_lock,
    _run_is_finished,
    beat_once,
)
from tools.mobile.heartbeat_writer import INTERVAL_S, Pace, _Writer  # noqa: F401

logger = logging.getLogger(__name__)

NO_TOKEN = (
    "A lease heartbeat needs the session token that holds the run; without it "
    "this would refresh somebody else's lease."
)

_LOCK = threading.Lock()
_WRITERS: dict = {}


def _ok(name: str, interval_s: float, state: tuple) -> dict:
    content = {
        "started": state[0],
        "run_id": name,
        "interval_s": interval_s,
        "reason": state[1],
    }
    return {"error": None, "content": content}


def _refuse(run_id: str, session_token: str) -> dict | None:
    """The refusal reply for a bad run id or a missing token, else ``None``."""
    if not run_store.valid_run_id(run_id):
        return {
            "error": "Refusing " + repr(str(run_id)[:40]) + " as a run id.",
            "content": None,
        }
    if not str(session_token or "").strip():
        return {"error": NO_TOKEN, "content": None}
    return None


# Start the heartbeat for *run_id*, held by *session_token*.
#
# ``{"error", "content": {"started", "run_id", "interval_s", "reason"}}``.
# A second call for a run that already has a live writer returns
# ``started=False, reason="already_running"`` rather than a second writer:
# two threads refreshing one lease is not twice as safe, it is twice as many
# things to stop.
#
# ``max_beats=0`` beats until stopped; a positive value is a BOUND, which is
# also what makes the thread terminate deterministically under test instead of
# a sleep the assertion has to outlast.
def start(run_id: str, session_token: str, pace: Pace | None = None) -> dict:
    """Start the heartbeat for *run_id*, held by *session_token*."""
    try:
        refused = _refuse(run_id, session_token)
        if refused is not None:
            return refused
        name = str(run_id)
        running, writer = _start_register(name, str(session_token).strip(), pace)
        if running is not None:
            return running
        writer.thread.start()
        return _ok(name, writer.interval_s, (True, "started"))
    except Exception as exc:
        logger.exception("mobile.heartbeat.start failed")
        return {"error": str(exc), "content": None}


def _start_register(name: str, token: str, pace: Pace | None):
    """Register a new writer, or return the already_running reply."""
    pace = pace or Pace()
    with _LOCK:
        existing = _WRITERS.get(name)
        if existing is not None and existing.thread.is_alive():
            return _ok(name, existing.interval_s, (False, "already_running")), None
        fixed = Pace(
            max(0.01, float(pace.interval_s or INTERVAL_S)),
            max(0, int(pace.max_beats or 0)),
        )
        writer = _Writer(name, token, fixed)
        _WRITERS[name] = writer
    return None, writer


def _stopped_reply(name: str, writer: _Writer | None) -> dict:
    if writer is None:
        content = {
            "stopped": False,
            "run_id": name,
            "beats": 0,
            "reason": "not_running",
        }
    else:
        content = {
            "stopped": True,
            "run_id": name,
            "beats": writer.beats,
            "reason": writer.stop_reason or "stopped",
        }
    return {"error": None, "content": content}


def stop(run_id: str, *, join_timeout: float = 2.0) -> dict:
    """Stop and JOIN this run's writer. ``{stopped, run_id, beats, reason}``."""
    try:
        name = str(run_id)
        with _LOCK:
            writer = _WRITERS.pop(name, None)
        if writer is not None:
            writer.stop()
            writer.thread.join(timeout=max(0.0, float(join_timeout)))
        return _stopped_reply(name, writer)
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

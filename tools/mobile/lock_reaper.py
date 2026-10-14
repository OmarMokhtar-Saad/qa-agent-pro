"""Release a device lock whose holder has gone quiet.

A run keeps its device lock until it reaches its report, and the heartbeat
refreshes the lease the whole time, so a run abandoned half way held the device
for good. The clock used here is the last STEP SUBMIT -- the one thing a live
agent does that a dead one does not -- and never the heartbeat.

Pure: the side effects (stop the heartbeat, end the run, free the lock) are
injected through :class:`ReleasePorts`, so this module touches no disk.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

from tools.mobile import run_verdict
from tools.mobile.lock_reaper_args import (  # noqa: F401
    IdleQuery,
    ReleaseCtx,
    ReleasePorts,
)

logger = logging.getLogger(__name__)

#: Seconds without a step submit after which a NEW run may take the device from
#: its holder. Five minutes is longer than any single model turn plus a slow
#: screen settle, so a working agent is never cut off, and short enough that an
#: abandoned run does not hold a tester's phone for the rest of the day.
IDLE_RELEASE_TIMEOUT_S = 300

#: Runs whose last activity this process remembers. The oldest is dropped first;
#: the persisted lease stamp still covers a dropped one.
MAX_TRACKED_RUNS = 64

_activity: OrderedDict = OrderedDict()


def _stamp(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def touch_activity(run_id: str, now: Optional[float] = None) -> None:
    """Record that the agent just acted for *run_id*. Call on every step submit."""
    if not run_id:
        return
    _activity[run_id] = time.time() if now is None else float(now)
    _activity.move_to_end(run_id)
    while len(_activity) > MAX_TRACKED_RUNS:
        _activity.popitem(last=False)


def forget(run_id: str) -> None:
    _activity.pop(run_id, None)


def idle_seconds(
    run_id: str, persisted_ts: Optional[float] = None, now: Optional[float] = None
) -> Optional[float]:
    """Seconds since the newest known activity, or None when none is known.

    The newest of the in-process stamp and the persisted lease stamp wins. A
    holder with no stamp at all is UNKNOWN, not idle: it is never released.
    """
    stamps = [
        s
        for s in (_stamp(_activity.get(run_id)), _stamp(persisted_ts))
        if s is not None
    ]
    if not stamps:
        return None
    current = time.time() if now is None else float(now)
    return max(0.0, current - max(stamps))


def is_idle(run_id: str, query: IdleQuery | None = None) -> bool:
    q = query or IdleQuery()
    limit = IDLE_RELEASE_TIMEOUT_S if q.timeout_s is None else q.timeout_s
    idle = idle_seconds(run_id, q.persisted_ts, q.now)
    return idle is not None and idle >= limit


@dataclass(frozen=True)
class ReleaseNotice:
    """What was released, for the message the NEW run's caller reads."""

    run_id: str
    serial: str
    idle_s: float

    def reason(self) -> str:
        return "released after " + str(int(self.idle_s)) + " s without agent activity"

    def message(self) -> str:
        where = "device " + self.serial if self.serial else "the device"
        return (
            "Released "
            + where
            + " from run "
            + self.run_id
            + ": it had not acted for "
            + str(int(self.idle_s))
            + " s (the limit is "
            + str(IDLE_RELEASE_TIMEOUT_S)
            + " s), so that run was ended as unverified and its lock freed."
        )


async def call_port(port: Any, *args: Any) -> Any:
    """Call a port that may be sync or async."""
    result = port(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


#: What a failing port (heartbeat, verdict write, lock release) may raise. Caught
#: so one failing step never leaves the device locked; anything else is a bug.
PORT_FAILURES = (
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
)


async def _finalize(ports: ReleasePorts, run_id: str, verdict: Any) -> None:
    """A verdict that cannot be written must not keep the device locked."""
    try:
        await call_port(ports.finalize, run_id, verdict)
    except PORT_FAILURES:
        logger.warning("lock_reaper: could not finalize %s", run_id, exc_info=True)


async def _free(ports: ReleasePorts, notice: ReleaseNotice, verdict: Any) -> bool:
    try:
        await call_port(ports.stop_heartbeat, notice.run_id)
        await _finalize(ports, notice.run_id, verdict)
        released = await call_port(ports.release_lock, notice.serial, notice.run_id)
    except PORT_FAILURES:
        logger.warning("lock_reaper: could not free %s", notice.run_id, exc_info=True)
        return False
    body = released.get("content") if isinstance(released, dict) else None
    if isinstance(body, dict) and body.get("released") is False:
        # The run is ended, but the device was not freed: never say it was.
        logger.warning("lock_reaper: %s held no lock to free", notice.run_id)
        forget(notice.run_id)
        return False
    return True


def _idle_notice(run_id: str, ctx: ReleaseCtx) -> Optional[ReleaseNotice]:
    """The notice for *run_id* when it has been idle long enough, else None."""
    idle = idle_seconds(run_id, ctx.persisted_ts, ctx.now)
    if idle is None or idle < IDLE_RELEASE_TIMEOUT_S:
        return None
    return ReleaseNotice(run_id, ctx.serial, idle)


async def release_if_idle(
    holder_run_id: str, ctx: ReleaseCtx
) -> Optional[ReleaseNotice]:
    """Free *serial* from *holder_run_id* if it has been idle long enough.

    Returns the notice (its ``message()`` names the old run), or None when the
    holder is active, unknown, or could not be freed.
    """
    notice = _idle_notice(holder_run_id, ctx)
    if notice is None:
        return None
    verdict = run_verdict.synthesize(notice.reason())
    if not await _free(ctx.ports, notice, verdict):
        return None
    forget(holder_run_id)
    return notice

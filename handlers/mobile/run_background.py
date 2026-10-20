"""A setup call the host abandoned keeps running; the next call adopts it (fix/1530).

fix/1520 gave the device back when the host (Cursor) cancelled a
``qa_mobile_test`` setup call, which threw away the work in flight (an App
Tester download/install) and made the next call start over. Now the setup body
runs as its own task, shielded from the caller:

* the host abandons the call -> the task goes on and the device stays held for
  the run it is building (a hand-off keeps the hold; a failure gives it back
  in the body's own ``finally``);
* the next call for the same device/app ADOPTS the job: it waits on the task's
  completion (an event, no polling) under a deadline ceiling and returns the
  run-start reply, or the reason the setup failed;
* a finished job for another device/app is RECLAIMED first, so an orphan never
  blocks a different request;
* no follow-up at all: the run is a normal run, so the existing idle release
  (``lock_reaper``, measured from the activity stamped at completion here)
  frees it on the next conflicting call. A failed job holds nothing.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field

from handlers.mobile import run_background_flags as flags
from tools.mobile import lock_reaper, locks, locks_devices, session_finish

#: Longest an adopting call waits for a still-running setup (a cold install).
ADOPT_DEADLINE_S = 600.0

_NOTE = (
    "(Picked up the setup your previous call started before it was "
    "cancelled; nothing was downloaded twice.)\n\n"
)
_STILL = "The setup from your previous call is still running; call again."


@dataclass
class Job:
    key: tuple
    task: asyncio.Future
    orphaned: bool = False
    flags: dict = field(default_factory=dict)
    done_at: float = 0.0
    serial: str = ""
    run_id: str = ""
    reply: str = ""
    error: BaseException | None = field(default=None, repr=False)


_JOBS: dict[tuple, Job] = {}
_NOTES: dict[tuple, str] = {}  # reply prefix for the fresh job of a key

#: Longest a restart waits for the cancelled setup to hand its device back.
RESTART_WAIT_S = 30.0


def key_of(req: object) -> tuple:
    """What makes two setup calls the same request: device, app and source."""
    names = ("serial", "avd", "app", "package", "source")
    return tuple(str(getattr(req, n, "") or "") for n in names)


def jobs() -> list[Job]:
    return list(_JOBS.values())


def _drop(job: Job) -> None:
    if _JOBS.get(job.key) is job:
        del _JOBS[job.key]


def _holder_of(serial: str) -> str:
    name = locks_devices.device_lock_name(serial)
    with locks._MUTEX:
        held = locks._HELD.get(name)
        return held.owner if held else ""


def _settle(job: Job, task: asyncio.Future) -> None:
    """Record the outcome; a run that came out of it is stamped active now."""
    job.done_at = time.time()
    if task.cancelled():
        job.error = asyncio.CancelledError()
    elif task.exception() is not None:
        job.error = task.exception()
    else:
        job.reply, job.serial, handed = task.result()
        job.run_id = _holder_of(job.serial) if handed else ""
        if job.run_id:
            lock_reaper.touch_activity(job.run_id)


def _outcome(job: Job) -> str:
    _drop(job)
    if job.error is not None:
        raise job.error
    return job.reply


async def launch(req: object, body: Awaitable) -> str:
    """Run *body* (``-> (reply, serial, handed_off)``) so a cancel cannot stop it."""
    job = Job(key_of(req), asyncio.ensure_future(body), flags=flags.of(req))
    _JOBS.setdefault(job.key, job)
    prefix = _NOTES.pop(job.key, "")
    job.task.add_done_callback(lambda t: _settle(job, t))
    try:
        await asyncio.wait({job.task})  # waiting never cancels the task
    except asyncio.CancelledError:
        job.orphaned = True
        raise
    return prefix + _outcome(job)


async def reclaim(job: Job) -> None:
    """Free a finished orphan's run so it cannot block another request."""
    _drop(job)
    if not job.run_id or _holder_of(job.serial) != job.run_id:
        return
    ctx = lock_reaper.ReleaseCtx(
        job.serial,
        session_finish.idle_ports(),
        now=time.time() + lock_reaper.IDLE_RELEASE_TIMEOUT_S,
    )
    await lock_reaper.release_if_idle(job.run_id, ctx)


def _stale(job: Job) -> bool:
    """A failed orphan nobody asked for, kept only for its reason."""
    aged = time.time() - job.done_at > lock_reaper.IDLE_RELEASE_TIMEOUT_S
    return job.task.done() and not job.run_id and aged


def _gone(job: Job) -> bool:
    """A finished orphan whose run no longer holds its device has nothing left."""
    return bool(job.run_id) and _holder_of(job.serial) != job.run_id


async def _adopt_wait(job: Job) -> str | None:
    done, _ = await asyncio.wait({job.task}, timeout=ADOPT_DEADLINE_S)
    if not done:
        return _STILL
    if _gone(job):
        _drop(job)
        return None
    return _NOTE + _outcome(job)


async def _restart(job: Job, names: list[str]) -> None:
    """Drop an orphan whose device-state flags changed; the caller starts fresh."""
    if not job.task.done():
        job.task.cancel()
        await asyncio.wait({job.task}, timeout=RESTART_WAIT_S)
    if job.task.done():
        await reclaim(job)
    else:  # still unwinding: it may hold the device, so only forget it
        _drop(job)
    _NOTES[job.key] = flags.note(names)


def discard_note(req: object) -> None:
    """The restart note of *req* will not be shown (no launch followed adopt)."""
    _NOTES.pop(key_of(req), None)


async def opened(req: object, opening: Awaitable) -> tuple:
    """Await the pre-launch step ``-> (..., early_reply)``; no launch means no note."""
    try:
        result = await opening
    except BaseException:
        discard_note(req)
        raise
    if result[-1]:
        discard_note(req)
    return result


async def adopt(req: object) -> str | None:
    """The finished (or still-running) setup of this request, else ``None``.

    Finished orphans for other requests are reclaimed on the way.
    """
    mine = key_of(req)
    for job in jobs():
        if job.key != mine and job.orphaned and job.task.done():
            await reclaim(job)
        elif _stale(job):
            _drop(job)
    found = _JOBS.get(mine)
    if found is None or not found.orphaned:
        return None
    diff = flags.changed(found.flags, flags.of(req))
    if diff:
        await _restart(found, diff)
        return None
    return await _adopt_wait(found)

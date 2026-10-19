"""Replay engine: the tester's keyboard given back when the agent goes quiet.

The QA keyboard stays selected while a run is working: the fast screen reader
lives inside it, so handing it back between calls would make every screen read
the slow uiautomator dump. But a chat that simply stops (the model waits, the
tester walks away) used to leave it selected for good -- the tester's phone had
a keyboard with no keys, and every restore needed a tool call to happen.

So the idle clock runs between tool calls, never during one: the run calls
(``qa_mobile_test``, ``qa_submit_mobile_step``) go through :func:`busy`, and
once no such call is in flight for
``IDLE_GIVE_BACK_S`` each run this process selected the keyboard for gets the
tester's keyboard back from its own record. The next call of such a run takes
the QA keyboard again (:func:`_retake`) so its reads are fast once more.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from config.settings_guard import never_raise
from tools.mobile import ime_session, imes_record
from tools.mobile import locks as mobile_locks
from tools.mobile.replay import keyboard_hold

logger = logging.getLogger("tools.mobile.executor")

#: Seconds with no mobile tool call in flight before the keyboard goes back.
#: Longer than an ordinary model turn, so a working run is not switched back
#: and forth, and far shorter than the 300 s device-lock reaper.
IDLE_GIVE_BACK_S = 120.0

#: In-flight call count, the pending idle timer and the running give-back.
_STATE: dict = {"busy": 0, "timer": None, "task": None}

#: ``run_id -> serial`` of runs whose keyboard went back idle (to retake).
_GIVEN_BACK: dict[str, str] = {}


def reset() -> None:
    """Forget the idle clock and every pending retake (a fresh process)."""
    timer = _STATE["timer"]
    if timer is not None:
        timer.cancel()
    _STATE.update(busy=0, timer=None, task=None)
    _GIVEN_BACK.clear()


@contextlib.asynccontextmanager
async def busy(run_id: str) -> AsyncIterator[None]:
    """Hold the idle clock for one tool call; it restarts when the last one ends."""
    _enter()
    try:
        await _settled()
        await _retake(str(run_id or ""))
        yield
    finally:
        _leave()


def _enter() -> None:
    _STATE["busy"] += 1
    timer = _STATE["timer"]
    _STATE["timer"] = None
    if timer is not None:
        timer.cancel()


def _leave() -> None:
    _STATE["busy"] = max(0, _STATE["busy"] - 1)
    if _STATE["busy"] or not imes_record.live_runs():
        return
    with never_raise(logger, "keyboard idle: timer not set", exc_info=True):
        loop = asyncio.get_running_loop()
        _STATE["timer"] = loop.call_later(IDLE_GIVE_BACK_S, _fire)


async def _settled() -> None:
    """Wait for a give-back already under way, so a retake never races it."""
    task = _STATE["task"]
    if task is not None and not task.done():
        with never_raise(logger, "keyboard idle: give-back failed", exc_info=True):
            await asyncio.shield(task)


def _fire() -> None:
    """The idle timer ran out: start the give-back unless a call began meanwhile."""
    _STATE["timer"] = None
    if not _STATE["busy"]:
        _STATE["task"] = asyncio.ensure_future(give_back_all())


async def give_back_all() -> list[str]:
    """Give back every keyboard this process holds; the runs that got one back."""
    done: list[str] = []
    for run_id in imes_record.live_runs():
        with never_raise(logger, "keyboard idle: give-back skipped", exc_info=True):
            if await _give_back(run_id):
                done.append(run_id)
    return done


async def _give_back(run_id: str) -> bool:
    """Restore *run_id*'s remembered keyboard; True when it went back."""
    record = dict(imes_record.read_record(run_id) or {})
    serial = str(record.get("serial") or "")
    keyboard_hold.forget(run_id)
    restored = await ime_session.restore(run_id)
    logger.info("keyboard idle: %s gave back on %s: %s", run_id, serial, restored)
    if restored.get("error") and record:
        # restore drops the record even when the device refused; the run is
        # still alive, so it stays owed for its end or the stale sweep.
        imes_record.write_record(run_id, record)
    if not serial or restored.get("error"):
        return False
    _GIVEN_BACK[run_id] = serial
    return True


async def _retake(run_id: str) -> None:
    """Select the QA keyboard again for a run that had it given back idle."""
    serial = _GIVEN_BACK.pop(run_id, "") if run_id else ""
    if not serial or not mobile_locks.names_held_by(run_id):
        return
    with never_raise(logger, "keyboard idle: retake skipped", exc_info=True):
        ready = await ime_session.ensure_ready(serial, run_id)
        logger.info("keyboard idle: %s retook on %s: %s", run_id, serial, ready)

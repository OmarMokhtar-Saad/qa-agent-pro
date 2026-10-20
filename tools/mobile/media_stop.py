"""Stopping and abandoning a recorder: the wall-clock-bounded half of the lane."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from tools.mobile import adb
from tools.mobile.media_consts import MAX_STOP_WAIT_S

logger = logging.getLogger("tools.mobile.media")

#: This module's OWN handle on ``asyncio.wait_for``.
#:
#: The stop's timeout is the fact a test must be able to observe, and the honest
#: way to observe it is to replace THIS name -- module-local, and restored by
#: ``monkeypatch`` at the end of the test -- rather than ``asyncio.wait_for``
#: itself. Patching the stdlib attribute reaches every coroutine that awaits a
#: timeout anywhere in the same test process, so a spy meant to read one number
#: silently re-times unrelated code.
wait_for = asyncio.wait_for


# With the pid the spawn read (fix/1560) the interrupt reaches THIS recorder
# only, so a step's stop leaves the run-long recorder of ``media_run`` running.
# Without one it falls back to every ``screenrecord`` on the device; a run
# segment it closes that way is pulled and the next one starts.
async def signal_recorder(serial: str, pid: object = None) -> None:
    """Interrupt one recorder by pid, else every ``screenrecord`` on the device."""
    if isinstance(pid, int) and pid > 0:
        await adb.shell(serial, ["kill", "-INT", str(pid)], MAX_STOP_WAIT_S)
        return
    await adb.shell(serial, ["pkill", "-INT", "screenrecord"], MAX_STOP_WAIT_S)


def _failed(done: dict) -> bool:
    """Whether an ``adb.shell`` result is an adb error or a non-zero exit."""
    body = done.get("content")
    return bool(done.get("error")) or bool(isinstance(body, dict) and body.get("rc"))


async def kill_recorder(serial: str, pid: object = None) -> None:
    """Kill, on the device, a recorder that ignored its interrupt (pid only)."""
    if not (isinstance(pid, int) and pid > 0):
        return
    done = await adb.shell(serial, ["kill", "-9", str(pid)], MAX_STOP_WAIT_S)
    if _failed(done):
        logger.warning(
            "mobile.media: could not kill recorder pid %d on the device", pid
        )


async def delete_on_device(serial: str, remote: str) -> bool:
    """``rm -f`` a clip on the device, once more if the first try failed."""
    for _ in range(2):
        done = await adb.shell(serial, ["rm", "-f", remote], MAX_STOP_WAIT_S)
        if not _failed(done):
            return True
    logger.warning("mobile.media: could not delete %s on the device", remote)
    return False


# A killed screenrecord leaves an unplayable file, which is why this sends an
# interrupt on the device FIRST and gives the recorder a window to close its
# container, rather than killing the adb client outright.
#
# But the window is :data:`MAX_STOP_WAIT_S`, not ``adb.DEFAULT_TIMEOUT_S``, and
# the client IS killed once it expires. A device with no ``pkill``, or one
# where the interrupt never reaches the recorder, would otherwise block the
# step for the full adb default -- thirty seconds of a tester's wall clock,
# every step, for a picture that is already lost.
#
# The interrupt targets the handle's pid; only a handle without one falls back
# to the device-global ``pkill -INT screenrecord``.
async def _stop_recorder(serial: str, handle: dict) -> bool:
    """Interrupt the recorder so it CLOSES its container, then reap it."""
    if handle.get("proc") is None:
        return False
    await signal_recorder(serial, handle.get("pid"))
    return await reap_recorder(handle)


async def reap_recorder(handle: dict) -> bool:
    """Wait (bounded) for an already-interrupted recorder to exit; kill it past that."""
    proc = handle.get("proc")
    if proc is None:
        return False
    with never_raise():
        await wait_for(proc.communicate(), timeout=MAX_STOP_WAIT_S)
        return True
    with never_raise():
        proc.kill()
        await wait_for(proc.communicate(), timeout=MAX_STOP_WAIT_S)
    return False


# The one path where a clip is thrown away rather than named. When
# ``executor.replay`` returns an ERROR, ``case_runner`` returns it without
# checkpointing: there is no step result to attach a picture to and no
# terminal screen to key a frame by, so there is nowhere a state could be
# shown. What there still IS, though, is a live device process -- and a
# recorder nobody stops outlives the step, at which point the NEXT step's
# device-global ``pkill -INT screenrecord`` closes the WRONG recording.
#
# Bounded by :data:`MAX_STOP_WAIT_S` like every other stop, and never raises: an
# error path must not acquire a second way to fail.
async def abandon(serial: str, handle: object) -> dict:
    """Stop a recorder and DELETE its file, storing no record at all."""
    held = handle if isinstance(handle, dict) else {}
    with never_raise(
        logger, "mobile.media.abandon failed: %s", level=logging.WARNING, with_exc=True
    ):
        if held.get("proc") is not None:
            await _stop_recorder(serial, held)
        remote = str(held.get("remote") or "")
        if remote:
            await adb.shell(serial, ["rm", "-f", remote], MAX_STOP_WAIT_S)
    return {"error": None, "content": None}

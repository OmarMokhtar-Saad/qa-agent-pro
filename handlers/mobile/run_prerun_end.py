"""Part of the mobile_run split (see handlers/mobile/run.py): ending the pre-run hold.

The ``finally`` of ``_mobile_new_run`` lands here. A call that handed its hold
to a run keeps nothing; every other ending gives the device back, and it has
to do so even when the host has CANCELLED the call.
"""

from __future__ import annotations

import asyncio
import logging

import tools.mobile.device_lock as device_lock
import tools.mobile.session_finish as session_finish
from tools.mobile import prerun_inflight
from tools.mobile.device_lock import DeviceRelease

logger = logging.getLogger("tools.mcp_handlers")


# Notes for ``_mobile_finish_pre_run``:
# Only ever THIS call's own label, so a sibling call's hold
# cannot be released here. `as_holder` is stated because the
# placeholder has no lease to present -- this call minted the
# label and is unambiguously its owner.
# The pre-run placeholder never typed, but it may own the
# early a11y reader: the restore inside this gives that back
# (a no-op without a record). It goes through the
# same door anyway, so the "no direct release in this
# file" pin can be absolute rather than a list of
# exceptions somebody has to keep correct.
#
# CANCEL-SAFE. When the host gives up on the call, anyio's
# cancellation is level-triggered: every await below raises again, so
# an unshielded finish died at its first await and the device stayed
# held for the life of the server (pid 34583 held R5CR207KY7H for ever
# on 2026-10-20). The clean-up runs as its own task (shielded, so it
# finishes in the background), and the cancel branch gives the device
# back SYNCHRONOUSLY -- no await between the cancel and the release --
# before re-raising. The restore then finds nothing held and skips; the
# sync release is the one line that must not depend on an await.
# KNOWN: the background restore runs AFTER the sync release, so another
# chat may already hold the device while this owner's a11y/IME state is
# put back; it touches only what this owner recorded, and the next
# call's own setup re-arms whatever it needs.
async def _mobile_finish_pre_run(owner: str) -> None:
    """Give back this call's own pre-run hold, for a call that handed nothing off."""
    try:
        await asyncio.shield(_finish_pre_run_task(owner))
    except asyncio.CancelledError:
        device_lock.release_device_lock(owner, DeviceRelease(as_holder=True))
        raise
    finally:
        prerun_inflight.leave(owner)


def _finish_pre_run_task(owner: str) -> asyncio.Future:
    """The pre-run clean-up as a task that outlives a cancelled call."""
    finishing = session_finish.finish_device(owner, as_holder=True)
    task = asyncio.ensure_future(finishing)
    task.add_done_callback(_log_finish_outcome)
    return task


def _log_finish_outcome(task: asyncio.Future) -> None:
    """Log a clean-up that failed after its call was cancelled; never raise."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("mobile pre-run clean-up failed: %r", exc)


async def _mobile_end_pre_run(owner: str, handed_off: bool) -> None:
    """End this call's setup phase: hand-off keeps the hold, anything else gives it back."""
    if handed_off:
        prerun_inflight.leave(owner)
        return
    await _mobile_finish_pre_run(owner)

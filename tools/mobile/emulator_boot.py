"""``boot``: ensure an AVD is running and booted, re-attaching when it already is."""

from __future__ import annotations

import logging
import time
from typing import NamedTuple

from tools.mobile.emulator_host import host
from tools.mobile.emulator_wait import (
    BootOpts,
    _await_serial,
    _never_listed_error,
    boot_timeout_s,
)

logger = logging.getLogger("tools.mobile.emulator")


class Launch(NamedTuple):
    """How the emulator that :func:`boot` returns came to be running."""

    reattached: bool
    pid: int


def _booted(avd: str, serial: object, how: Launch) -> dict:
    """The success result :func:`boot` returns."""
    return {
        "error": None,
        "content": {
            "serial": serial,
            "avd": str(avd),
            "reattached": how.reattached,
            "pid": how.pid,
        },
    }


async def _reattach(avd: str, serial: object, timeout: int) -> dict:
    """Wait for an already-running emulator to finish booting."""
    waited = await host().wait_boot(str(serial), timeout)
    if waited.get("error"):
        return waited
    return _booted(avd, serial, Launch(reattached=True, pid=0))


async def _spawn_and_wait(avd: str, timeout: int) -> dict:
    """Spawn *avd*, wait for it to list in adb, then for it to finish booting."""
    started = await host().start(avd)
    if started.get("error"):
        return started
    pid = int((started.get("content") or {}).get("pid") or 0)
    budget = int(timeout or boot_timeout_s())

    deadline = time.monotonic() + budget
    serial, failure = await _await_serial(avd, deadline)
    if failure:
        return failure
    if not serial:
        return {"error": _never_listed_error(avd, pid, budget), "content": None}
    remaining = max(1, int(deadline - time.monotonic()))
    waited = await host().wait_boot(serial, remaining, BootOpts(avd=avd))
    if waited.get("error"):
        return waited
    return _booted(avd, serial, Launch(reattached=False, pid=pid))


async def _ready_or_start(avd: str, timeout: int) -> dict:
    """Re-attach to *avd* when it is running, else spawn it and wait."""
    # A FAILED probe is not "no emulator": `or {}` collapsed the two, and
    # this function's next move is to spawn one. That is D1 exactly -- a
    # broken adb starting a second emulator over the tester's own -- and it
    # is latent here only because nothing calls boot() in production today.
    probe = await host().find_running(avd)
    if probe.get("error"):
        return probe
    running = probe.get("content") or {}
    if running.get("serial"):
        return await _reattach(avd, running["serial"], timeout)
    return await _spawn_and_wait(avd, timeout)


# ``{"error", "content": {"serial", "avd", "reattached", "pid"}}``.
#
# The kill-switch is enforced in ``start``, which is where the spawn
# happens. It also refuses the RE-ATTACH branch, and that is deliberate for a
# reason worth stating properly: re-attach is not a terminal read-only
# operation, it is the first step of a run -- the caller goes on to poll the
# device and install the IME onto it. ``find_running``, ``stop`` and
# ``preflight`` stay unguarded so a tester can still INSPECT with the lane
# off.
#
# THE GUARD IS HERE, NOT ONLY IN ``start``, and the docstring above used
# to claim the re-attach branch was refused while the code returned it. That
# branch returns BEFORE ``start`` is ever reached, so delegating was a promise
# this function did not keep: with the lane off, a machine that happened to
# have an emulator running got a live serial back, and the caller's next moves
# are to poll it and install the IME onto it.
#
# An earlier note here said mutation showed a copy of the guard could be
# deleted with the suite green. That was true, and it was evidence the guard
# was UNTESTED rather than redundant -- the only test asserting it reached the
# real ``adb``, so it passed on any machine with no emulator running and
# failed on a tester's. It is hermetic now, and deleting either guard goes
# red.
async def boot(avd: str, timeout: int = 0) -> dict:
    """Ensure *avd* is running and booted, re-attaching when it already is."""
    try:
        host().ensure_adb_first_on_path()
        return await _ready_or_start(avd, timeout)
    except Exception as exc:
        logger.exception("mobile.emulator.boot failed")
        return {"error": str(exc), "content": None}

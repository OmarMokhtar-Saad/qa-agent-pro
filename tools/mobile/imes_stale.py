"""Give back a keyboard a crashed run left selected."""

from __future__ import annotations

import logging

from tools.mobile import ime, imes_sweep

logger = logging.getLogger("tools.mobile.ime_session")


def _kept(result: dict, runs: list, previous: str) -> dict:
    """The restore failed: the records are KEPT and the failure said."""
    # KEPT, so the next run can try again, and SAID, because the
    # tester is the only one who can fix it in Settings if no
    # next run comes.
    detail = (
        "could not give back the keyboard an earlier run left "
        "selected (" + str(result["error"])[:160] + "). It will "
        "be tried again next time."
    )
    return imes_sweep._sweep_result(imes_sweep._Sweep(False, detail, runs, previous))


def _no_target(found: list, runs: list) -> dict:
    """Every record names our own keyboard: spent, so cleared."""
    # NOTHING TO GO BACK TO, so these records are spent: every candidate
    # names our own keyboard. Cleared, because keeping them would make
    # every later sweep re-report a crash nobody can act on -- and
    # unlike the failure below, there is no information here to lose.
    imes_sweep._clear_all(found)
    detail = (
        "an earlier run left a record naming no keyboard to go "
        "back to, so nothing was changed"
    )
    return imes_sweep._sweep_result(imes_sweep._Sweep(False, detail, runs))


def _done(previous: str, runs: list) -> dict:
    """The keyboard a crashed run left selected was given back."""
    detail = (
        "restored the keyboard a crashed run left selected ("
        + ime.display_id(previous)
        + ")"
    )
    return imes_sweep._sweep_result(imes_sweep._Sweep(True, detail, runs, previous))


async def _restore_found(serial: str, found: list) -> dict:
    """Give back the keyboard named by *found*, clearing records only on success."""
    previous = imes_sweep._earliest_previous(found)
    runs = [r["run_id"] for r in found]
    if not previous:
        return _no_target(found, runs)
    # RESTORE FIRST, CLEAR ONLY ON SUCCESS. The first version cleared here,
    # before the restore, and a review caught what that costs: a device that
    # dropped off adb for the second the `ime set` takes left no record, so
    # no later run could retry -- the fix deleted, on its own failure path,
    # the one thing it was built to read.
    result = await ime.restore_previous(str(serial), previous)
    if result.get("error"):
        return _kept(result, runs, previous)
    imes_sweep._clear_all(found)
    return _done(previous, runs)


def _refusal(found: list, undead: list) -> dict:
    """ONE refusal for all three conditions."""
    # ONE refusal for all three, deliberately: they are the same answer
    # to the tester -- something may still be using this device, so
    # nothing was touched -- and three differently-worded refusals for
    # one outcome is how a reader starts believing they mean different
    # things. `undead` includes every record written before the pid
    # field existed, which is correct and self-healing: no pid is not
    # evidence of a dead run, and the next run writes one.
    detail = (
        "a previous run's keyboard record exists but its holder "
        "could not be confirmed dead, so the device was left alone"
    )
    runs = undead or [r["run_id"] for r in found]
    return imes_sweep._sweep_result(imes_sweep._Sweep(False, detail, runs))


async def _sweep(serial: str, skip_run_id: str) -> dict:
    """Scan, refuse while a holder is unconfirmed dead, else restore."""
    found = imes_sweep.stale_records(serial, skip_run_id=skip_run_id)
    if not found:
        detail = "no earlier run left a keyboard selected on this device"
        return imes_sweep._sweep_result(imes_sweep._Sweep(False, detail, []))
    undead = imes_sweep._unconfirmed_holders(serial, skip_run_id, found)
    if undead is not None:
        return _refusal(found, undead)
    return await _restore_found(serial, found)


# Give back a keyboard a crashed run left selected on *serial*.
#
# ``{"restored": bool, "detail": str, "previous": str, "runs": [...]}``.
#
# **A held device is left alone, and that is the whole safety argument.**
# A record whose device is currently held belongs to a LIVE run, and
# restoring the tester's keyboard underneath a run that is mid-replay would
# be a worse defect than the one this fixes -- the run would type into a
# keyboard that stopped listening halfway through. `locks.device_holders` is
# asked by name, and anything other than a clear "free" is treated as held:
# a probe that failed is not evidence the device is idle.
#
# Reported, never silent. A tester whose keyboard changes back must be told
# which run left it and what it was set to, or the tool looks like it is
# acting on its own.
async def restore_stale(serial: str, *, skip_run_id: str = "") -> dict:
    """Give back a keyboard a crashed run left selected on *serial*."""
    try:
        return await _sweep(serial, skip_run_id)
    except Exception as exc:
        logger.exception("mobile.ime_session.restore_stale failed")
        return {"error": str(exc), "content": None}

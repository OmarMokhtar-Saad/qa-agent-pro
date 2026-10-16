"""Mobile session: finish_device, device-lock release and run-end bookkeeping. Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from config.settings_guard import never_raise
from tools.mobile import (
    a11y,
    animations,
    heartbeat,
    ime,
    ime_session,
    lock_reaper,
    locks,
    preflight,
    run_finalize,
    run_lease_release,
    stop_goal,
    stop_run,
)
from tools.mobile.device_lock import (
    DeviceRelease,
    release_device_lock,
    serial_of,
)
from tools.mobile.replay import keyboard_hold
from tools.mobile.session_finish_ports import (  # noqa: F401
    _finalizer,
    awaits_tester,
    last_checkpoint_ts,
    release_select_lock,
    run_ended,
    take_select_lock,
)
from tools.mobile.session_finish_reply import _finish_device_reply
from tools.mobile.session_finish_sweep import sweep_stale

logger = logging.getLogger("tools.mobile.session")


async def preflight_for(resolved: object) -> dict:
    """Run the full preflight for a resolved run. Returns EVERY check."""
    body = resolved if isinstance(resolved, dict) else {}
    return await preflight.check(
        str(body.get("package") or ""), str(body.get("serial") or "")
    )


def _active_run(run_id: str) -> stop_run.ActiveRun:
    """One live run for the stop tool, with the goal verdict its record proves."""
    goal = stop_goal.goal_verdict(run_id)
    return stop_run.ActiveRun(run_id, serial_of(run_id), trace_verdict=goal)


def stop_ports() -> stop_run.StopPorts:
    """The ports ``stop_run.stop`` needs, from this module's own helpers.

    UNVERIFIED: ``active_runs`` sees only this process's heartbeats. ``finalize``
    writes ``manifest["final"]`` once (idempotent), so a second stop records no
    second verdict; ``release`` frees only a device this run really holds.
    """

    def active_runs(serial=None):
        runs = [_active_run(r) for r in heartbeat.active()]
        return [r for r in runs if not serial or r.serial == serial]

    return SimpleNamespace(
        active_runs=active_runs,
        stop_heartbeat=heartbeat.stop,
        finalize=_finalizer(run_finalize.STOP_TESTER),
        release=release_restoring,
    )


def idle_ports() -> lock_reaper.ReleasePorts:
    """The ports ``lock_reaper.release_if_idle`` needs to end an idle holder.

    Same providers as :func:`stop_ports`, with the verdict recorded as
    ``released_idle``: the new run, not the tester, ended the old one.
    """
    return SimpleNamespace(
        stop_heartbeat=heartbeat.stop,
        finalize=_finalizer(run_finalize.STOP_IDLE),
        release_lock=release_restoring,
    )


def release_if_held(serial: str, run_id: str) -> dict:
    """Compare-and-release: free ``serial`` only if ``run_id`` holds it.

    A run that holds nothing, or holds a DIFFERENT device, releases nothing, so a
    stop or an idle release can never free the device of the run that took over.
    An empty ``serial`` means the caller does not know the device: the owner's
    own holds are released, exactly what ``release_device_lock`` does by owner.
    """
    held = locks.names_held_by(run_id)
    name = locks.device_lock_name(serial)
    if not held or (name and name not in held):
        return {"error": None, "content": {"released": False, "reason": "not held"}}
    released = release_device_lock(run_id, DeviceRelease(force=True))
    run_lease_release.mark_released(run_id)
    return released


async def release_restoring(serial: str, run_id: str) -> dict:
    """:func:`release_if_held` that first gives back the keyboard the run holds."""
    keyboard_hold.forget(run_id)
    if locks.names_held_by(run_id):
        with never_raise(
            logger, "mobile.session.release: keyboard skipped", exc_info=True
        ):
            await ime_session.restore(run_id)
    return release_if_held(serial, run_id)


# **The one chokepoint for ending a run's hold on a device.** Every
# run-teardown path in ``handlers`` calls this rather than
# :func:`release_device_lock` directly, and a test pins that there is no direct
# call left in that file -- so a fifth teardown path added later cannot skip
# the restore by reaching for the lock function.
#
# **Why this exists instead of putting the restore inside
# :func:`release_device_lock`.** That function is synchronous and takes no
# serial, deliberately: its own comment says "BY OWNER, NOT BY NAME. The caller
# does not always know the serial". ``ime.restore_previous`` is async and needs
# one. Putting the restore inside it would have meant changing the signature of
# a function with eight callers, or driving an event loop from a sync function
# that several callers already invoke from inside one. So the lock function is
# untouched and this wraps it.
#
# The restore is a no-op for a run that never typed -- which is most of them,
# and all of the pre-run placeholder releases -- because
# ``ime_session.restore`` reads a record that only ``ensure_ready`` writes.
#
# **Not called by the capture lane or the heartbeat.** Those take the device
# lock for a certificate install, a proxy or a lease sweep; they never touched
# an input method and have nothing to give back, so they keep calling the
# synchronous function directly.
#
# **What this does NOT cover:** a hard crash or a killed process calls nothing
# at all -- :func:`release_device_lock` says it, and it is true here too. There
# is no reaper in this lane, so a run killed outright leaves the QA keyboard
# selected until something else runs. The doctor's per-device "selected" row is
# what surfaces that afterwards.
#
# **Keywords are forwarded verbatim, not restated.** ``**release_kwargs`` goes
# straight to :func:`release_device_lock`, so this wrapper holds no copy of
# that function's defaults -- a copy would pin today's values and drift the
# day one of them changed. It also means a caller that passes only ``lease=``
# still calls a one-keyword function, which is what every existing caller and
# every existing test double expects.
async def finish_device(owner: str, **release_kwargs) -> dict:
    """Give the tester's keyboard back, THEN give the emulator back."""
    restores = await _restore_run_state(str(owner))
    stale = await _sweep_stale_state(str(owner))
    released = release_device_lock(str(owner), DeviceRelease(**release_kwargs))
    run_lease_release.mark_released(str(owner))
    return _finish_device_reply(released, restores, stale)


def _forget_keyboard_hold(owner: str) -> None:
    """The run's keyboard hold ends with the run, even when the device is gone."""
    with never_raise(
        logger, "mobile.session.finish_device: hold skipped", exc_info=True
    ):
        keyboard_hold.forget(owner)


async def _restore_run_state(owner: str) -> tuple[dict, dict, dict]:
    """Disarm, then restore keyboard, animations and a11y: ``(ime, anim, a11y)``."""

    _forget_keyboard_hold(owner)
    # DISARM first (G4): the nonce is forgotten even when the device is gone.
    with never_raise(
        logger, "mobile.session.finish_device: disarm skipped", exc_info=True
    ):
        await ime.disarm(serial_of(owner) or "")
    if locks.is_provisioning_owner(owner):
        return {}, {}, {}
    restored = await ime_session.restore(owner)
    # The run's animation scales go back too, on the same chokepoint and for the
    # same reason: every teardown path reaches this function.
    anim = await animations.restore(owner)
    # The a11y service goes off and the tester's accessibility settings come back.
    a11y_restored = await a11y.restore(owner)
    return restored, anim, a11y_restored


async def _sweep_stale_state(owner: str) -> tuple[dict, dict, dict]:
    """Restore what earlier crashed runs left: ``(ime, animations, a11y)``."""
    return await sweep_stale(serial_of, owner)

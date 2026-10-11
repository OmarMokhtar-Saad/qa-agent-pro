"""Mobile session: finish_device, device-lock release and run-end bookkeeping. Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from tools.mobile import (
    case_runner,
    heartbeat,
    lock_reaper,
    locks,
    preflight,
    run_finalize,
    run_store,
    stop_run,
)
from tools.mobile.device_lock import (
    release_device_lock,
    serial_of,
)

logger = logging.getLogger("tools.mobile.session")


async def preflight_for(resolved: object) -> dict:
    """Run the full preflight for a resolved run. Returns EVERY check."""
    body = resolved if isinstance(resolved, dict) else {}
    return await preflight.check(
        str(body.get("package") or ""), str(body.get("serial") or "")
    )


def stop_ports() -> stop_run.StopPorts:
    """The ports ``stop_run.stop`` needs, from this module's own helpers.

    UNVERIFIED: ``active_runs`` sees only this process's heartbeats. ``finalize``
    writes ``manifest["final"]`` once (idempotent), so a second stop records no
    second verdict; ``release`` frees only a device this run really holds.
    """

    def active_runs(serial=None):
        runs = [stop_run.ActiveRun(r, serial_of(r)) for r in heartbeat.active()]
        return [r for r in runs if not serial or r.serial == serial]

    return SimpleNamespace(
        active_runs=active_runs,
        stop_heartbeat=heartbeat.stop,
        finalize=_finalizer(run_finalize.STOP_TESTER),
        release=release_if_held,
    )


def idle_ports() -> lock_reaper.ReleasePorts:
    """The ports ``lock_reaper.release_if_idle`` needs to end an idle holder.

    Same providers as :func:`stop_ports`, with the verdict recorded as
    ``released_idle``: the new run, not the tester, ended the old one.
    """
    return SimpleNamespace(
        stop_heartbeat=heartbeat.stop,
        finalize=_finalizer(run_finalize.STOP_IDLE),
        release_lock=release_if_held,
    )


def last_checkpoint_ts(run_id: str) -> float | None:
    """When the newest case checkpoint of *run_id* was written, or None.

    The idle release's persisted clock for a holder this process no longer
    tracks (evicted from the reaper table, or the server restarted). Never the
    lease stamp: the heartbeat refreshes that for as long as the run lives.
    Never raises.
    """
    try:
        cases = run_store.run_path(run_id) / run_store.CASES_DIR
        stamps = [p.stat().st_mtime for p in cases.glob("TC-*.json")]
    except (OSError, ValueError):
        return None
    return max(stamps, default=None)


def awaits_tester(run_id: str) -> bool:
    """True when a case of *run_id* is paused for the tester (no verdict yet)."""
    cases = (run_store.list_cases(run_id) or {}).get("content") or []
    return any(
        c.get("status") == case_runner.NEEDS_TESTER and not c.get("verdict")
        for c in cases
    )


def run_ended(run_id: str) -> bool:
    """True when the run's manifest records a stop (tester stop or idle release)."""
    read = run_store.read_manifest(run_id).get("content")
    return bool(run_finalize.stop_text(read))


def _finalizer(stop: str):
    """A ``finalize(run_id, verdict)`` port that records ``stop`` with the verdict."""

    def finalize(run_id, verdict):
        return run_finalize.finalize(run_id, verdict, stop=stop)

    return finalize


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
    return release_device_lock(run_id, force=True)


def take_select_lock(owner: str) -> dict:
    """Serialise DEVICE SELECTION, which is a question about the MACHINE.

    Selection reads ``adb devices``, may adopt a foreign emulator and may spawn
    the shared default one, and it runs BEFORE any serial exists -- so it cannot
    be keyed by a device and must not be. This is the lock this lane has always
    taken at the top of a new run; what changed is that it is given back the
    moment a device has been named, instead of covering the install, the
    preflight and the run.
    """
    return locks.acquire(locks.EMULATOR_LOCK, owner=str(owner))


def release_select_lock(owner: str) -> dict:
    """Give selection back. The caller minted the label, so it IS the holder."""
    return locks.release(locks.EMULATOR_LOCK, owner=str(owner), as_holder=True)


async def finish_device(owner: str, **release_kwargs) -> dict:
    """Give the tester's keyboard back, THEN give the emulator back.

    **The one chokepoint for ending a run's hold on a device.** Every
    run-teardown path in ``handlers`` calls this rather than
    :func:`release_device_lock` directly, and a test pins that there is no direct
    call left in that file -- so a fifth teardown path added later cannot skip
    the restore by reaching for the lock function.

    **Why this exists instead of putting the restore inside
    :func:`release_device_lock`.** That function is synchronous and takes no
    serial, deliberately: its own comment says "BY OWNER, NOT BY NAME. The caller
    does not always know the serial". ``ime.restore_previous`` is async and needs
    one. Putting the restore inside it would have meant changing the signature of
    a function with eight callers, or driving an event loop from a sync function
    that several callers already invoke from inside one. So the lock function is
    untouched and this wraps it.

    The restore is a no-op for a run that never typed -- which is most of them,
    and all of the pre-run placeholder releases -- because
    ``ime_session.restore`` reads a record that only ``ensure_ready`` writes.

    **Not called by the capture lane or the heartbeat.** Those take the device
    lock for a certificate install, a proxy or a lease sweep; they never touched
    an input method and have nothing to give back, so they keep calling the
    synchronous function directly.

    **What this does NOT cover:** a hard crash or a killed process calls nothing
    at all -- :func:`release_device_lock` says it, and it is true here too. There
    is no reaper in this lane, so a run killed outright leaves the QA keyboard
    selected until something else runs. The doctor's per-device "selected" row is
    what surfaces that afterwards.

    **Keywords are forwarded verbatim, not restated.** ``**release_kwargs`` goes
    straight to :func:`release_device_lock`, so this wrapper holds no copy of
    that function's defaults -- a copy would pin today's values and drift the
    day one of them changed. It also means a caller that passes only ``lease=``
    still calls a one-keyword function, which is what every existing caller and
    every existing test double expects.
    """
    restores = await _restore_run_state(str(owner))
    stale = await _sweep_stale_state(str(owner))
    released = release_device_lock(str(owner), **release_kwargs)
    return _finish_device_reply(released, restores, stale)


async def _restore_run_state(owner: str) -> tuple[dict, dict, dict]:
    """Disarm, then restore keyboard, animations and a11y: ``(ime, anim, a11y)``."""
    from tools.mobile import a11y, animations, ime, ime_session

    # DISARM first (G4): the nonce is forgotten even when the device is gone.
    try:
        await ime.disarm(serial_of(owner) or "")
    except Exception:
        logger.debug("mobile.session.finish_device: disarm skipped", exc_info=True)
    restored = await ime_session.restore(owner)
    # The run's animation scales go back too, on the same chokepoint and for the
    # same reason: every teardown path reaches this function.
    anim = await animations.restore(owner)
    # The a11y service goes off and the tester's accessibility settings come back.
    a11y_restored = await a11y.restore(owner)
    return restored, anim, a11y_restored


async def _sweep_stale_state(owner: str) -> tuple[dict, dict, dict]:
    """Restore what earlier crashed runs left: ``(ime, animations, a11y)``."""
    from tools.mobile import a11y, animations, ime_session

    a11y_stale = {}
    anim_stale = {}
    # AND ANY KEYBOARD AN EARLIER RUN CRASHED OUT OF. Done here as well as at
    # run start because the two reach different machines: run start covers the
    # tester who runs the lane again, this covers the run that is ending on a
    # device whose previous holder died. Both go through the same function, so
    # neither can drift from the held-device check that makes it safe.
    swept = {}
    try:
        # `serial_of` is THE producer of a run's device for the locking path --
        # its own docstring says so -- rather than a second derivation off the
        # restore envelope, which does not carry one.
        serial = serial_of(str(owner))
        if serial:
            swept = (
                await ime_session.restore_stale(serial, skip_run_id=str(owner)) or {}
            ).get("content") or {}
            anim_stale = (
                await animations.restore_stale(serial, skip_run_id=str(owner)) or {}
            ).get("content") or {}
            a11y_stale = (
                await a11y.restore_stale(serial, skip_run_id=str(owner)) or {}
            ).get("content") or {}
    except Exception:
        logger.debug("mobile.session.finish_device: stale sweep skipped", exc_info=True)
    return swept, anim_stale, a11y_stale


def _finish_device_reply(released: dict, restores: tuple, stale: tuple) -> dict:
    """The ``finish_device`` envelope from the release result and both restores."""
    restored, anim, a11y_restored = restores
    swept, anim_stale, a11y_stale = stale
    return {
        "error": None,
        "content": {
            "released": bool((released.get("content") or {}).get("released")),
            "release": released.get("content") or {},
            # Carried rather than swallowed: a restore that failed is a device
            # left on the QA keyboard, and the reply a tester reads is the only
            # place that can say so.
            "ime": restored.get("content")
            or {"restored": False, "detail": str(restored.get("error") or "")},
            # A SECOND VALUE WITH A SECOND NAME, not a caveat on the first.
            # "the keyboard this run displaced" and "a keyboard an earlier,
            # crashed run displaced" are different facts with different owners,
            # and a reader that had to tell them apart from one field would be
            # guessing.
            "ime_stale": swept,
            "animations": anim.get("content")
            or {"restored": False, "detail": str(anim.get("error") or "")},
            "animations_stale": anim_stale,
            "a11y": a11y_restored.get("content")
            or {"restored": False, "detail": str(a11y_restored.get("error") or "")},
            "a11y_stale": a11y_stale,
        },
    }

"""Mobile session: device provisioning and liveness. Cut from tools/mobile/session_device.py (w17 sessA)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile import adb, emulator
from tools.mobile.session_core_state import STATE_BOOTING, STATE_NO_AVD  # noqa: F401

logger = logging.getLogger("tools.mobile.session")


#: The longest this module will ever wait on the device inside one tool call.
#: Well under a client's ~50s tool timeout, and passed EXPLICITLY to
#: ``wait_boot`` -- whose own default is 240s, so an omitted keyword is the
#: defect this constant exists to prevent.
DEVICE_WAIT_BUDGET_S = 20


class DeviceWant(NamedTuple):
    """What ``ensure_device`` is asked for: a serial, or an AVD to attach to."""

    avd: str = ""
    serial: str = ""
    budget_s: int = DEVICE_WAIT_BUDGET_S
    locale: str = ""


def _no_avd_refusal() -> dict:
    """The refusal ``ensure_device`` hands back when no AVD is named."""
    return {
        "error": (
            "No emulator (AVD) is named. Call `qa_mobile_test` again with "
            "`avd` set to one of the AVDs in Android Studio's Device "
            "Manager. `emulator=list` shows them and `emulator=create` "
            "makes one from an installed system image."
        ),
        "content": None,
    }


def _pointer(state: str, serial: str, detail: str = "") -> dict:
    """The ``{state, serial, detail}`` reply of the boot helpers."""
    return {
        "error": None,
        "content": {"state": state, "serial": serial, "detail": detail},
    }


# 1200, not 400: wait_boot's message may carry the
# virtualization note, which is 295 chars on top of a
# ~173-char timeout line. At 400 every delivery of that
# note was cut mid-parenthesis and lost the half that
# says WHERE to enable it -- a producer composing a
# message no consumer delivers whole.
async def _wait_device_ready(serial: str, budget: int) -> dict:
    """Wait for ``serial`` to boot: ``ready``, or a ``booting`` pointer on timeout."""
    waited = await emulator.wait_boot(
        serial, timeout=budget, opts=emulator.BootOpts(tunable=False)
    )
    if waited.get("error"):
        return _pointer(STATE_BOOTING, serial, str(waited["error"])[:1200])
    return _pointer("ready", serial)


# Only this path spawns, so only this path can set the locale from
# the first frame. A device we ADOPTED is handled by `apply_locale`,
# which reads back rather than assuming.
# B5: if the emulator this server spawned last time already DIED, say so in
# its own words instead of spawning over it and reporting "booting" again.
async def _spawn_device(name: str, locale: str) -> dict:
    """Start the AVD ``name`` detached and hand back a ``booting`` pointer."""
    gone = emulator.exit_report(name)
    if gone:
        return {"error": gone["error"], "content": None}
    started = await emulator.start(name, locale=str(locale or ""))
    if started.get("error"):
        return started
    pid = str((started.get("content") or {}).get("pid") or 0)
    detail = name + " was started (pid " + pid + "). "
    return _pointer(
        STATE_BOOTING, "", detail + "A cold emulator takes a minute or two."
    )


async def _find_or_spawn(name: str, budget: int, locale: str) -> dict:
    """Attach to the running AVD ``name``, else start it."""
    running = await emulator.find_running(name)
    if running.get("error"):
        return running
    serial = str((running.get("content") or {}).get("serial") or "")
    if serial:
        return await _wait_device_ready(serial, budget)
    return await _spawn_device(name, locale)


# ``{"error", "content": {"state", "serial", "detail"}}`` with ``state`` one of
# ``ready`` / :data:`STATE_BOOTING`.
#
# ``emulator.boot`` is deliberately NOT called: it polls to
# ``QA_MOBILE_BOOT_TIMEOUT_S`` (240s by default), which is four times a
# client's tool timeout, so a tester would see a dead editor rather than a
# message. The bounded pair is ``emulator.start`` (spawn, detached, returns at
# once) plus ``emulator.wait_boot`` with an EXPLICIT budget.
#
# That budget is :data:`DEVICE_WAIT_BUDGET_S`, and the setting does NOT move
# it, so both waits below pass ``BootOpts(tunable=False)``: the timeout message they
# hand to the tester must not offer a knob that cannot change this wait.
#
# A tester-chosen (or already-adopted) device: never probe for
# the hardcoded AVD and never spawn a second emulator under it.
#
# No house AVD to fall back on: auto-provisioning is retired
# (docs/RETIRED_CAPABILITIES.md -> 6) and the handler resolves the
# tester's own AVD before it gets here. Refuse rather than spawn
# `emulator -avd ""`.
async def ensure_device(want: DeviceWant | None = None) -> dict:
    """Attach to a booted emulator, or start one and hand back a pointer."""
    want = want or DeviceWant()
    try:
        name = str(want.avd or "").strip()
        budget = max(1, int(want.budget_s or DEVICE_WAIT_BUDGET_S))
        if want.serial:
            return await _wait_device_ready(want.serial, budget)
        if not name:
            return _no_avd_refusal()
        return await _find_or_spawn(name, budget, want.locale)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.ensure_device failed")
        return {"error": str(exc), "content": None}


def _alive(alive: bool, state: str) -> dict:
    """The ``device_alive`` envelope for a verdict and an ``adb`` state."""
    return {"error": None, "content": {"alive": alive, "state": state}}


# Keyed on the SERIAL rather than on ``emulator.find_running``'s AVD lookup
# deliberately: a resumed run knows the serial it was driving, and the AVD
# lookup needs a second round trip to a device that may already be gone.
async def device_alive(serial: str) -> dict:
    """``{alive, state}`` for one serial, answered by ``adb devices``."""
    try:
        wanted = str(serial or "").strip()
        if not wanted:
            return _alive(False, "unknown")
        listed = await adb.devices()
        if listed.get("error"):
            return _alive(False, "unknown")
        if any(str(found) == wanted for found in listed.get("content") or []):
            return _alive(True, "device")
        return _alive(False, "absent")
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.device_alive failed")
        return {"error": str(exc), "content": None}

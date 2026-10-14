"""Mobile session: device provisioning, install record and liveness. Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile import emulator, run_store
from tools.mobile.session_core_budget import Budget
from tools.mobile.session_core_state import STATE_BOOTING, STATE_NO_AVD
from tools.mobile.session_device_boot import (  # noqa: F401
    DEVICE_WAIT_BUDGET_S,
    DeviceWant,
    device_alive,
    ensure_device,
)
from tools.mobile.session_device_install import start_install  # noqa: F401
from tools.mobile.session_device_record import (  # noqa: F401
    INSTALL_FILE,
    INSTALL_PENDING_MAX_AGE_S,
    _install_state_path,
    _read_install_record,
)
from tools.mobile.session_device_state import InstallProbe, install_state  # noqa: F401

logger = logging.getLogger("tools.mobile.session")


class RunDeviceView(NamedTuple):
    """What ``ensure_run_device`` reports about the device of a run."""

    state: str
    serial: str
    detail: str = ""
    rebooted: bool = False


def _run_device_reply(view: RunDeviceView) -> dict:
    """The reply shape ``ensure_run_device`` returns."""
    return {
        "error": None,
        "content": {
            "state": view.state,
            "serial": view.serial,
            "detail": view.detail,
            "rebooted": view.rebooted,
        },
    }


def _no_run_refusal(run_id: str) -> dict:
    """The refusal for a run id that has no manifest on this machine."""
    return {
        "error": (
            "No run `"
            + str(run_id)
            + "` on this machine. `qa_mobile_status` with no run id "
            "lists the runs that do exist."
        ),
        "content": None,
    }


# ``{"error", "content": {"state", "serial", "detail", "rebooted"}}`` with
# ``state`` ``ready``, :data:`STATE_BOOTING`, or :data:`STATE_NO_AVD` (plus
# ``avd``) when the AVD this run came from no longer exists.
#
# A resume can arrive days later, in a new chat, on a machine that has been
# rebooted since -- at which point the manifest names a serial that no longer
# exists and every adb call fails with a device error a tester cannot act on.
# So the serial is CHECKED. A re-booted emulator can also come back on a
# DIFFERENT serial, which is why the fresh one is written back to the manifest
# and why ``rebooted`` is reported: the caller must re-run the preflight
# before producing a packet against a device it has not checked.
async def ensure_run_device(run_id: str, *, budget: Budget | None = None) -> dict:
    """Make sure the emulator THIS RUN was driving is up, re-booting if it died."""
    try:
        manifest = (run_store.read_manifest(run_id) or {}).get("content")
        if not isinstance(manifest, dict) or not manifest:
            return _no_run_refusal(run_id)
        serial = str(manifest.get("serial") or "")
        alive = (await device_alive(serial)).get("content") or {}
        if alive.get("alive"):
            return _run_device_reply(RunDeviceView("ready", serial))
        return await _reboot_run_device(run_id, manifest, budget)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.ensure_run_device failed")
        return {"error": str(exc), "content": None}


def _reboot_outcome(run_id: str, manifest: dict, state: dict) -> dict:
    """The reply once the re-boot answered: a pointer, or ready on the live serial."""
    serial = str(manifest.get("serial") or "")
    if str(state.get("state") or "") != "ready":
        return _run_device_reply(
            RunDeviceView(
                STATE_BOOTING,
                str(state.get("serial") or ""),
                str(state.get("detail") or ""),
            )
        )
    fresh = str(state.get("serial") or "")
    if fresh and fresh != serial:
        manifest["serial"] = fresh
        run_store.write_manifest(run_id, manifest)
    return _run_device_reply(RunDeviceView("ready", fresh or serial, rebooted=True))


def _no_avd_reply(avd: str) -> dict:
    """The reply for a run whose AVD no longer exists."""
    reply = _run_device_reply(RunDeviceView(STATE_NO_AVD, ""))
    reply["content"]["avd"] = avd
    return reply


# The device is gone and must be booted again -- but only from an AVD
# that still exists. Spawning a missing one "succeeds" (the launcher is
# detached) and then times out as a boot, which tells the tester to wait
# for something that will never come up. A failed listing (no SDK)
# falls through to the spawn, whose own error says what is missing.
async def _reboot_run_device(
    run_id: str, manifest: dict, budget: Budget | None
) -> dict:
    """Boot the run's AVD again (the serial is gone) and write back a new serial."""
    avd = str(manifest.get("avd") or "")
    listed = await emulator.list_avds()
    if not listed.get("error") and avd not in (listed.get("content") or []):
        return _no_avd_reply(avd)
    span = int(budget.remaining()) if budget is not None else DEVICE_WAIT_BUDGET_S
    ready = await ensure_device(
        DeviceWant(avd=avd, budget_s=max(1, min(DEVICE_WAIT_BUDGET_S, span)))
    )
    if ready.get("error"):
        return ready
    return _reboot_outcome(run_id, manifest, ready.get("content") or {})

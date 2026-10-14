"""Hardware-acceleration check and the no-devices diagnosis built on it."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from tools.mobile import platform_info
from tools.mobile.emulator_host import host
from tools.mobile.emulator_state import emulator_binary

logger = logging.getLogger("tools.mobile.emulator")

#: Seconds `emulator -accel-check` may run (B3). The Windows feature query in
#: platform_info takes up to 60 s and is deliberately NOT used to explain an
#: empty device list.
ACCEL_CHECK_TIMEOUT_S = 10


# Uses the fix TEXT only. ``platform_info.virtualization()`` and the privilege
# probe are not called: they can take a minute, and this runs while a tester
# waits for an empty device list to be explained.
def accel_fix(output: str = "") -> str:
    """The per-platform fix for a failed hardware-acceleration check."""
    if platform_info.is_windows():
        return platform_info._WHPX_FIX
    if platform_info.is_macos():
        return platform_info._HVF_FIX
    return (
        "On Linux the emulator needs KVM. Check `ls -l /dev/kvm`, install your "
        "distribution's qemu-kvm / cpu-checker package, make sure virtualization is "
        "enabled in the BIOS/UEFI, and add your user to the `kvm` group "
        "(`sudo usermod -aG kvm $USER`, then log out and back in)."
    )


def _accel_verdict(rc: int, text: str) -> dict:
    """The ``{"error", "content"}`` for an ``-accel-check`` exit code."""
    if rc == 124:
        return {
            "error": None,
            "content": {"ok": None, "timed_out": True, "output": text, "fix": ""},
        }
    if rc in (126, 127):
        return {"error": text or "emulator -accel-check could not run", "content": None}
    ok = rc == 0
    fix = "" if ok else accel_fix(text)
    return {
        "error": None,
        "content": {"ok": ok, "timed_out": False, "output": text, "fix": fix},
    }


def accel_check_sync() -> dict:
    """Bounded ``emulator -accel-check``. ``content.ok`` is None when undetermined."""
    try:
        binary = emulator_binary()
        if not binary:
            return {
                "error": "The Android emulator binary was not found.",
                "content": None,
            }
        rc, out, err = platform_info.run_sync(
            [binary, "-accel-check"], timeout=ACCEL_CHECK_TIMEOUT_S
        )
        return _accel_verdict(rc, " ".join((out + "\n" + err).split())[:400])
    except Exception as exc:
        logger.exception("mobile.emulator.accel_check failed")
        return {"error": str(exc), "content": None}


async def accel_check() -> dict:
    """``emulator -accel-check`` on a worker thread, bounded by ACCEL_CHECK_TIMEOUT_S."""
    return await asyncio.to_thread(host().accel_check_sync)


def _diag_core(diag: dict, avds: dict, accel: dict) -> dict:
    """Fill *diag* from the two probe answers."""
    if avds.get("error"):
        diag["avd_error"] = str(avds["error"])[:300]
    else:
        diag["avds"] = [str(n) for n in (avds.get("content") or [])][:20]
    if not accel.get("error"):
        diag["accel"] = accel.get("content")
    return diag


# ``{"avds": [...], "avd_error": str, "accel": {...} | None}``. Both probes run
# together under one bound of ``ACCEL_CHECK_TIMEOUT_S + 2`` s. Never raises and
# never calls ``platform_info.virtualization`` (B3).
async def diagnose_no_devices() -> dict:
    """Why ``qa_list_devices`` is empty: the AVDs on file and the acceleration verdict."""
    diag: dict = {"avds": [], "avd_error": "", "accel": None}
    with never_raise(
        logger, "mobile.emulator.diagnose_no_devices failed", exc_info=True
    ) as g:
        avds, accel = await asyncio.wait_for(
            asyncio.gather(host().list_avds(), host().accel_check()),
            timeout=ACCEL_CHECK_TIMEOUT_S + 2,
        )
    if g.failed:
        diag["avd_error"] = "The emulator diagnosis did not finish in time."
        return diag
    return _diag_core(diag, avds, accel)


def _avd_part(diag: dict) -> str:
    """The sentence naming the AVDs to start, or why there are none."""
    avds = list((diag or {}).get("avds") or [])
    if avds:
        return (
            "Emulators (AVDs) you can start: "
            + ", ".join(avds)
            + ". Call `qa_mobile_test` with `avd` set to one of them."
        )
    if (diag or {}).get("avd_error"):
        return str(diag["avd_error"])
    return (
        "No emulator (AVD) is defined. Create one in Android Studio's Device "
        "Manager, or attach a phone with USB debugging on."
    )


def _accel_part(diag: dict) -> str:
    """The sentence about hardware acceleration, or ``""`` when it is fine."""
    accel = (diag or {}).get("accel") or {}
    if accel.get("timed_out"):
        return (
            f"`emulator -accel-check` did not answer within {ACCEL_CHECK_TIMEOUT_S} s, so "
            "hardware acceleration is undetermined. " + accel_fix("")
        )
    if accel.get("ok") is False:
        return (
            "Hardware acceleration is NOT available ("
            + str(accel.get("output") or "no detail")[:300]
            + "). Fix: "
            + str(accel.get("fix") or accel_fix(""))
        )
    return ""


def render_no_devices_hint(diag: dict) -> str:
    """Tester-facing paragraph for an empty device list. Empty string if nothing to say."""
    return " ".join(p for p in (_avd_part(diag), _accel_part(diag)) if p)

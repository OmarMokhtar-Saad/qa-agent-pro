"""Asking adb and the emulator binary which AVDs exist and which are running."""

from __future__ import annotations

import asyncio
import logging

from tools.mobile import adb, platform_info
from tools.mobile.emulator_host import host
from tools.mobile.emulator_state import emulator_binary

logger = logging.getLogger("tools.mobile.emulator")

_NO_BINARY = (
    "The Android emulator binary was not found. Install Android "
    "Studio, then create an emulator in its Device Manager."
)


# Newer emulators print ``INFO | ...`` diagnostics on the same stdout, and an
# AVD name never contains a space or ``|``, so a line with either is not a name.
def _parse_avds(out: str) -> list[str]:
    return [
        line.strip()
        for line in out.splitlines()
        if line.strip() and " " not in line.strip() and "|" not in line
    ]


# AVD names ``emulator -list-avds`` reports. Blocking; see :func:`list_avds`.
# No emulator binary is an ERROR, never ``[]``: "no SDK" and "no AVD" get two
# different setup guides.
def list_avds_sync() -> dict:
    """AVD names ``emulator -list-avds`` reports. Blocking."""
    try:
        binary = emulator_binary()
        if not binary:
            return {"error": _NO_BINARY, "content": None}
        rc, out, err = platform_info.run_sync([binary, "-list-avds"], timeout=60)
        if rc != 0:
            return {
                "error": "emulator -list-avds failed: "
                + (err.strip() or "rc=" + str(rc))[:300],
                "content": None,
            }
        return {"error": None, "content": _parse_avds(out)}
    except Exception as exc:
        logger.exception("mobile.emulator.list_avds failed")
        return {"error": str(exc), "content": None}


# The subprocess would otherwise hold the event loop for up to its own 60 s
# timeout, and a caller's ``asyncio.wait_for`` (qa-doctor's) could not bound it.
async def list_avds() -> dict:
    """AVD names, asked on a worker thread."""
    return await asyncio.to_thread(host().list_avds_sync)


async def avd_name_of(serial: str) -> dict:
    """The AVD name behind an ``emulator-*`` serial, or ``""``."""
    result = await adb.raw(["-s", str(serial), "emu", "avd", "name"], timeout=15)
    if result.get("error"):
        return {"error": None, "content": ""}
    lines = [
        line.strip()
        for line in str((result["content"] or {}).get("out") or "").splitlines()
        if line.strip() and line.strip().upper() != "OK"
    ]
    return {"error": None, "content": lines[0] if lines else ""}


async def find_running(avd: str) -> dict:
    """``{"serial": ...}`` for an already-running *avd*, serial ``""`` if none."""
    try:
        listed = await adb.devices()
        if listed.get("error"):
            # NOT `serial: ""`. A failed probe is not the same fact as "no
            # emulator is running", and callers that spawn on the second answer
            # would otherwise start a device because adb was unreachable.
            return {"error": str(listed["error"]), "content": None}
        for serial in listed.get("content") or []:
            if not str(serial).startswith("emulator-"):
                continue
            named = await host().avd_name_of(serial)
            if str((named or {}).get("content") or "") == str(avd):
                return {"error": None, "content": {"serial": serial, "avd": str(avd)}}
        return {"error": None, "content": {"serial": "", "avd": str(avd)}}
    except Exception as exc:
        logger.exception("mobile.emulator.find_running failed")
        return {"error": str(exc), "content": None}


async def _running_rows(serials: list) -> list[dict]:
    """``{"serial", "avd"}`` for every ``emulator-*`` serial in *serials*."""
    out: list[dict] = []
    for serial in serials:
        if not str(serial).startswith("emulator-"):
            continue
        named = await host().avd_name_of(serial)
        out.append({"serial": serial, "avd": str((named or {}).get("content") or "")})
    return out


# Unlike :func:`find_running`, this does NOT filter by AVD -- it is how the
# device-selection stage sees a FOREIGN emulator (one not named by the caller)
# that a tester already has running, so a run can adopt it instead of spawning
# one. ``{"error", "content": [{"serial": ..., "avd": ...}, ...]}``.
async def list_running() -> dict:
    """Every booted ``emulator-*`` serial on this machine, with its AVD name."""
    try:
        listed = await adb.devices()
        if listed.get("error"):
            # An empty list means "nothing is booted", which sends the device
            # stage on to provisioning and a spawn. A broken adb must not be
            # able to say that: it reported "The emulator is still starting"
            # while starting a SECOND emulator over the tester's own (D1,
            # 2026-09-03). The caller already branches on `error`.
            return {"error": str(listed["error"]), "content": None}
        return {
            "error": None,
            "content": await _running_rows(listed.get("content") or []),
        }
    except Exception as exc:
        logger.exception("mobile.emulator.list_running failed")
        return {"error": str(exc), "content": None}


async def list_physical() -> dict:
    """Every ready non-emulator adb serial (a phone), as ``{"serial", "avd": ""}``.

    Lets the device stage adopt a connected phone instead of booting an
    emulator beside it. ``error`` is set when adb cannot be asked.
    """
    try:
        listed = await adb.devices()
        if listed.get("error"):
            return {"error": str(listed["error"]), "content": None}
        rows = [
            {"serial": str(s), "avd": ""}
            for s in listed.get("content") or []
            if not str(s).startswith("emulator-")
        ]
        return {"error": None, "content": rows}
    except Exception as exc:
        logger.exception("mobile.emulator.list_physical failed")
        return {"error": str(exc), "content": None}

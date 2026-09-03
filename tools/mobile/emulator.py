"""Emulator lifecycle: boot detached, wait for a real boot, re-attach, stop.

Two things here are lessons rather than features.

**Re-attach by AVD NAME.** The MCP server restarts far more often than an
emulator does (every code or ``.env`` edit), so "is one of my AVDs already
running?" must be answerable without state. ``adb -s <serial> emu avd name``
answers it from the device itself, so a resumed run finds the emulator it left
behind instead of booting a second one.

**adb-first-on-PATH.** Two adb binaries of different versions fight: each kills
the other's server and every command intermittently reports "device offline".
:func:`ensure_adb_first_on_path` puts the SDK's adb first for this process and
reports what was shadowing it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from config.settings import settings
from tools.mobile import adb, paths, platform_info, provisioner, sdk_locator

logger = logging.getLogger(__name__)

#: How often the boot poll asks the device.
POLL_INTERVAL_S = 2.0

#: A booted device answers this with ``1``.
BOOT_PROP = "sys.boot_completed"


def boot_timeout_s() -> int:
    """Operator-tunable boot budget (``QA_MOBILE_BOOT_TIMEOUT_S``)."""
    try:
        return max(30, int(settings.qa_mobile_boot_timeout_s))
    except Exception:  # pragma: no cover - coercer guarantees an int
        return 240


def _spawn(cmd: list[str], **kwargs) -> int:
    """The detached-spawn seam. Returns the child pid."""
    proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - argv list, no shell
    return int(proc.pid)


def ensure_adb_first_on_path(adb_path: str = "") -> dict:
    """Put the SDK's adb first on this process's PATH.

    ``{"error", "content": {"adb", "changed", "shadowed_by"}}``. Only this
    process's environment is touched -- nothing is written to a shell profile,
    because a tool that edits a tester's dotfiles to fix its own problem is
    worse than the problem.
    """
    try:
        resolved = str(adb_path or "") or adb.resolve_adb()
        if not resolved or not Path(resolved).is_file():
            return {
                "error": None,
                "content": {"adb": resolved, "changed": False, "shadowed_by": ""},
            }
        first = shutil.which(platform_info.exe("adb")) or ""
        if first and os.path.realpath(first) == os.path.realpath(resolved):
            return {
                "error": None,
                "content": {"adb": resolved, "changed": False, "shadowed_by": ""},
            }
        bin_dir = str(Path(resolved).parent)
        os.environ["PATH"] = bin_dir + os.pathsep + (os.environ.get("PATH") or "")
        return {
            "error": None,
            "content": {"adb": resolved, "changed": True, "shadowed_by": first},
        }
    except Exception as exc:
        logger.exception("mobile.emulator.ensure_adb_first_on_path failed")
        return {"error": str(exc), "content": None}


async def list_avds() -> dict:
    """AVD names ``emulator -list-avds`` reports."""
    try:
        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        binary = str((located.get("tools") or {}).get("emulator") or "")
        if not binary:
            return {
                "error": (
                    "The Android emulator binary was not found. Run the mobile "
                    "provisioner (or install Android Studio) first."
                ),
                "content": None,
            }
        rc, out, err = platform_info._run_sync([binary, "-list-avds"], timeout=60)
        if rc != 0:
            return {
                "error": "emulator -list-avds failed: "
                + (err.strip() or "rc=" + str(rc))[:300],
                "content": None,
            }
        names = [line.strip() for line in out.splitlines() if line.strip()]
        return {"error": None, "content": names}
    except Exception as exc:
        logger.exception("mobile.emulator.list_avds failed")
        return {"error": str(exc), "content": None}


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


async def find_running(avd: str = provisioner.AVD_NAME) -> dict:
    """``{"serial": ...}`` for an already-running *avd*, serial ``""`` if none."""
    try:
        listed = await adb.devices()
        if listed.get("error"):
            return {"error": None, "content": {"serial": "", "avd": str(avd)}}
        for serial in listed.get("content") or []:
            if not str(serial).startswith("emulator-"):
                continue
            named = await avd_name_of(serial)
            if str((named or {}).get("content") or "") == str(avd):
                return {"error": None, "content": {"serial": serial, "avd": str(avd)}}
        return {"error": None, "content": {"serial": "", "avd": str(avd)}}
    except Exception as exc:
        logger.exception("mobile.emulator.find_running failed")
        return {"error": str(exc), "content": None}


async def wait_boot(serial: str, timeout: int = 0) -> dict:
    """Poll until ``sys.boot_completed`` is ``1``.

    ``adb wait-for-device`` returns as soon as adbd answers, which is minutes
    before the launcher exists; every "the package is not installed" failure on
    a cold emulator traces back to trusting it.
    """
    try:
        budget = int(timeout or boot_timeout_s())
        deadline = time.monotonic() + budget
        last = ""
        while time.monotonic() < deadline:
            prop = await adb.getprop(serial, BOOT_PROP)
            last = (
                str(prop.get("content") or "")
                if not prop.get("error")
                else str(prop.get("error"))
            )
            if not prop.get("error") and str(prop.get("content") or "").strip() == "1":
                return {"error": None, "content": {"serial": serial, "booted": True}}
            await asyncio.sleep(POLL_INTERVAL_S)
        return {
            "error": (
                "The emulator did not finish booting within "
                + str(budget)
                + "s (last "
                + BOOT_PROP
                + "="
                + repr(last[:60])
                + "). Raise QA_MOBILE_BOOT_TIMEOUT_S, or start the AVD from "
                "Android Studio once to warm its snapshot."
            ),
            "content": None,
        }
    except Exception as exc:
        logger.exception("mobile.emulator.wait_boot failed")
        return {"error": str(exc), "content": None}


async def start(avd: str = provisioner.AVD_NAME) -> dict:
    """Spawn *avd* detached and return AT ONCE. ``{"error", "content": {"pid"}}``.

    Extracted from :func:`boot` (2026-09-02, Phase 3) because a caller inside an
    MCP tool call cannot afford ``boot``'s poll: it runs to
    ``QA_MOBILE_BOOT_TIMEOUT_S`` (240s by default), which is roughly four times
    a client's tool timeout, so a tester would see a dead editor rather than a
    message. ``boot`` now calls THIS, so there is exactly one place that spawns
    an emulator and the bounded and unbounded paths cannot diverge.

    THE KILL-SWITCH LIVES HERE, not on ``boot``: this is the innermost public
    function that spawns, and a guard on a caller is only as good as the list
    of callers. When this function was extracted the guard stayed on ``boot``
    and this became an unguarded spawn site in the same commit that was meant
    to close that class.
    """
    try:
        if not settings.qa_mobile_run_enabled:
            return {
                "error": (
                    "Refusing to start an emulator: the mobile lane needs "
                    "`QA_MOBILE_RUN_ENABLED=true` in `.env`. Nothing was "
                    "launched."
                ),
                "content": None,
            }
        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        binary = str((located.get("tools") or {}).get("emulator") or "")
        if not binary:
            return {
                "error": (
                    "The Android emulator binary was not found, so "
                    + str(avd)
                    + " cannot be started. Run the mobile provisioner first."
                ),
                "content": None,
            }
        paths.ensure_tree()
        command = [
            binary,
            "-avd",
            str(avd),
            "-no-snapshot-save",
            "-no-boot-anim",
        ]
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        kwargs.update(platform_info.detach_kwargs())
        return {"error": None, "content": {"pid": _spawn(command, **kwargs)}}
    except Exception as exc:
        logger.exception("mobile.emulator.start failed")
        return {"error": str(exc), "content": None}


async def boot(avd: str = provisioner.AVD_NAME, timeout: int = 0) -> dict:
    """Ensure *avd* is running and booted, re-attaching when it already is.

    ``{"error", "content": {"serial", "avd", "reattached", "pid"}}``.

    The kill-switch is enforced in :func:`start`, which is where the spawn
    happens. It also refuses the RE-ATTACH branch, and that is deliberate for a
    reason worth stating properly: re-attach is not a terminal read-only
    operation, it is the first step of a run -- the caller goes on to poll the
    device and install the IME onto it. ``find_running``, ``stop`` and
    ``preflight`` stay unguarded so a tester can still INSPECT with the lane
    off.

    No guard of its own: this delegates to :func:`start`, and mutation showed a
    copy here could be deleted with the suite green. Two copies of one rule is
    two things to keep in sync, which this module has already been bitten by.
    """
    try:
        ensure_adb_first_on_path()
        running = (await find_running(avd)).get("content") or {}
        if running.get("serial"):
            waited = await wait_boot(str(running["serial"]), timeout)
            if waited.get("error"):
                return waited
            return {
                "error": None,
                "content": {
                    "serial": running["serial"],
                    "avd": str(avd),
                    "reattached": True,
                    "pid": 0,
                },
            }
        started = await start(avd)
        if started.get("error"):
            return started
        pid = int((started.get("content") or {}).get("pid") or 0)
        budget = int(timeout or boot_timeout_s())

        deadline = time.monotonic() + budget
        serial = ""
        while time.monotonic() < deadline and not serial:
            await asyncio.sleep(POLL_INTERVAL_S)
            found = (await find_running(avd)).get("content") or {}
            serial = str(found.get("serial") or "")
        if not serial:
            return {
                "error": (
                    str(avd)
                    + " was started (pid "
                    + str(pid)
                    + ") but never appeared in `adb devices` within "
                    + str(budget)
                    + "s. Start it once from Android Studio to see the "
                    "emulator's own error."
                ),
                "content": None,
            }
        waited = await wait_boot(serial, max(1, int(deadline - time.monotonic())))
        if waited.get("error"):
            return waited
        return {
            "error": None,
            "content": {
                "serial": serial,
                "avd": str(avd),
                "reattached": False,
                "pid": pid,
            },
        }
    except Exception as exc:
        logger.exception("mobile.emulator.boot failed")
        return {"error": str(exc), "content": None}


async def stop(serial: str) -> dict:
    """Ask the emulator to exit (``adb -s <serial> emu kill``)."""
    result = await adb.raw(["-s", str(serial), "emu", "kill"], timeout=30)
    if result.get("error"):
        return result
    return {"error": None, "content": {"serial": str(serial), "stopped": True}}


def python_is_windows() -> bool:
    """Small readable seam for the Windows branches the tests monkeypatch."""
    return sys.platform == "win32"

"""The preflight run: start the early probes, run every step, summarise."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from tools.device_manager import valid_package_name
from tools.mobile import adb, ime
from tools.mobile import preflight_dns as dns_mod
from tools.mobile.preflight_adb import (
    _step_adb_first_on_path,
    _step_adb_responds,
    _step_virtualization,
)
from tools.mobile.preflight_boot import _step_emulator_booted
from tools.mobile.preflight_dns import _step_device_dns
from tools.mobile.preflight_host import (
    _step_cache_ownership,
    _step_free_disk,
    _step_host_privileges,
)
from tools.mobile.preflight_ime import _step_ime_pinned, _step_ime_probes
from tools.mobile.preflight_package import _step_package_installed
from tools.mobile.preflight_rows import _Dev, _mark_typing_blocking, _Req
from tools.mobile.preflight_screen_step import _step_device_screen

logger = logging.getLogger("tools.mobile.preflight")


# The three input-method reads, started only when the IME manifest is ready:
# the steps that read them make the same condition check.
def _early_ime(serial: str, tasks: dict) -> None:
    if bool(((ime.manifest_status() or {}).get("content") or {}).get("ok")):
        tasks["ime_installed"] = asyncio.ensure_future(ime.installed(serial))
        tasks["ime_selected"] = asyncio.ensure_future(ime.current_ime(serial))
        tasks["ime_oracle"] = asyncio.ensure_future(ime.probe(serial))


# Start the read-only probes of steps 4b-9 at once. ``{}`` without a serial.
# Only probes whose step WILL make the call are started, under the same
# conditions that step checks, so no answer is fetched that nothing reads.
# None of them changes the device: a ping, a package list, and three reads
# of input-method state. Never raises; a probe that cannot be started is
# simply left to its step.
def _start_early_probes(serial: str, target_package: str) -> dict:
    tasks: dict = {}
    if not serial:
        return tasks
    with never_raise(
        logger, "mobile.preflight: early probes not started", exc_info=True
    ):
        tasks["dns"] = asyncio.ensure_future(dns_mod.dns_probe(serial))
        package = str(target_package or "").strip()
        if package and valid_package_name(package):
            tasks["packages"] = asyncio.ensure_future(adb.installed_packages(serial))
        _early_ime(serial, tasks)
    return tasks


# `ok` is "nothing that BLOCKS a run failed". `failing` stays every
# failure, because the renderer still shows an advisory one with its fix
# -- a tester should read that typing is unavailable, and then be allowed
# to run anyway.
def _blocking_names(checks: list[dict], needs_typing: bool) -> tuple[list, list]:
    failing = [record["name"] for record in checks if not record["ok"]]
    if needs_typing:
        _mark_typing_blocking(checks)
    blocking = [
        record["name"]
        for record in checks
        if not record["ok"] and record.get("blocking", True)
    ]
    return failing, blocking


# The ``content`` of ``check``'s answer, from every row gathered.
def _summarise(checks: list[dict], resolved_serial: str, needs_typing: bool) -> dict:
    failing, blocking = _blocking_names(checks, needs_typing)
    advisory = [name for name in failing if name not in blocking]
    return {
        "ok": not blocking,
        "blocking": blocking,
        "advisory": advisory,
        "serial": resolved_serial,
        "checks": checks,
        "failing": failing,
    }


# Steps 1-4a. Virtualization is computed at step 4a, once the serial is
# known, and inserted at position 0 so the row order is unchanged: a
# physical handset has no use for the emulator-accelerator answer.
async def _run_device_steps(checks: list[dict], req: _Req) -> str:
    serials = await _step_adb_responds(checks)
    _step_adb_first_on_path(checks)
    resolved_serial = await _step_emulator_booted(checks, serials, req.serial)
    _step_virtualization(checks, resolved_serial)
    return resolved_serial


# Steps 4b-9. Each step awaits ITS early task at its own place, inside its
# own try, so the row order and every step's failure handling are exactly
# what the serial version produced; a step that finds no task makes the
# call itself.
async def _run_probe_steps(checks: list[dict], target_package: str, dev: _Dev) -> None:
    await _step_device_screen(checks, target_package, dev)
    await _step_device_dns(checks, dev)
    await _step_package_installed(checks, target_package, dev)
    pinned = _step_ime_pinned(checks)
    await _step_ime_probes(checks, pinned, dev)
    _step_free_disk(checks)
    _step_cache_ownership(checks)
    _step_host_privileges(checks)


# Run every step in order, appending to *checks*; return the resolved serial.
# *early* is filled with the started probe tasks so the caller can cancel
# any task no step reached.
async def _run_steps(checks: list[dict], req: _Req, early: dict) -> str:
    resolved_serial = await _run_device_steps(checks, req)
    # THE READ-ONLY DEVICE PROBES, STARTED TOGETHER (S13). Steps 4b-9 each
    # made one adb round trip and waited for it before the next began, so a
    # slow resolver (the DNS ping has its own multi-second budget) held up
    # four probes that do not depend on it.
    early.update(_start_early_probes(resolved_serial, req.target_package))
    await _run_probe_steps(checks, req.target_package, _Dev(resolved_serial, early))
    return resolved_serial


# A task no step reached (the function failed part-way) is cancelled
# rather than left to finish against a device nobody is reading.
def _cancel_unread(early: dict) -> None:
    for task in early.values():
        task.cancel()


# Run EVERY check and return all of them.
# ``{"error", "content": {"ok", "serial", "checks": [...], "failing": [...]}}``.
# ``error`` is reserved for this function itself failing; a failed CHECK is a
# normal result with ``ok=False``, because a refusal a tester can act on is
# not an error condition.
async def check(
    target_package: str = "", serial: str = "", needs_typing: bool = False
) -> dict:
    """Run EVERY check and return all of them, as ``{"error", "content"}``."""
    checks: list[dict] = []
    early: dict = {}
    try:
        req = _Req(target_package, str(serial or ""))
        resolved_serial = await _run_steps(checks, req, early)
        content = _summarise(checks, resolved_serial, needs_typing)
        return {"error": None, "content": content}
    except Exception as exc:
        logger.exception("mobile.preflight.check failed")
        return {"error": str(exc), "content": None}
    finally:
        _cancel_unread(early)

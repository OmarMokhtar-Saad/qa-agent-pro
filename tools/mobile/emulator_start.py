"""Spawning an emulator detached, putting the SDK's adb first, and stopping one."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from tools.mobile import adb, emulator_state, paths, platform_info
from tools.mobile.emulator_host import host
from tools.process_env import get_env, set_env

logger = logging.getLogger("tools.mobile.emulator")


def _adb_row(resolved: str, changed: bool, shadowed_by: str) -> dict:
    """The ``ensure_adb_first_on_path`` success result."""
    return {
        "error": None,
        "content": {"adb": resolved, "changed": changed, "shadowed_by": shadowed_by},
    }


# ``{"error", "content": {"adb", "changed", "shadowed_by"}}``. Only this
# process's environment is touched -- nothing is written to a shell profile,
# because a tool that edits a tester's dotfiles to fix its own problem is worse
# than the problem.
def ensure_adb_first_on_path(adb_path: str = "") -> dict:
    """Put the SDK's adb first on this process's PATH."""
    try:
        resolved = str(adb_path or "") or adb.resolve_adb()
        if not resolved or not Path(resolved).is_file():
            return _adb_row(resolved, False, "")
        first = shutil.which(platform_info.exe("adb")) or ""
        if first and os.path.realpath(first) == os.path.realpath(resolved):
            return _adb_row(resolved, False, "")
        bin_dir = str(Path(resolved).parent)
        set_env("PATH", bin_dir + os.pathsep + (get_env("PATH") or ""))
        return _adb_row(resolved, True, first)
    except Exception as exc:
        logger.exception("mobile.emulator.ensure_adb_first_on_path failed")
        return {"error": str(exc), "content": None}


# THE ONE MECHANISM THAT WORKS UNPRIVILEGED. `persist.sys.locale` is a
# `persist.*` property, so `adb shell setprop` needs root that the
# `google_apis_playstore` user build does not give -- and even where the
# write lands, the running system keeps its old configuration until it
# restarts. Set at SPAWN, the device comes up in the language from its
# first frame, and it survives every later reboot of that AVD.
#
# APPENDED, never inserted: `test_boot_spawns_detached_when_nothing_is_
# running` reads `cmd[1:3]` by index.
def _spawn_command(binary: str, avd: str, locale: str) -> list[str]:
    """The emulator command line for *avd*, with the locale prop when valid."""
    command = [binary, "-avd", str(avd), "-no-snapshot-save", "-no-boot-anim"]
    wanted = str(locale or "").strip()
    if wanted and adb.LOCALE_TAG.match(wanted):
        command += ["-prop", adb.PERSIST_LOCALE_PROP + "=" + wanted]
    return command


def _spawn_kwargs(avd: str) -> tuple[dict, str]:
    """The detached-spawn keyword arguments and the log path ('' if none)."""
    kwargs = platform_info.detached_devnull_kwargs()
    log_path = emulator_state._emulator_log_path(str(avd))
    if log_path:
        kwargs["log_path"] = log_path
    return kwargs, log_path


def _launch(avd: str, binary: str, locale: str) -> int:
    """Spawn *avd* and record it; returns the pid."""
    command = _spawn_command(binary, avd, locale)
    kwargs, log_path = _spawn_kwargs(avd)
    pid = host().spawn(command, **kwargs)
    # Recorded AFTER the spawn returned, so a failed spawn is never listed
    # as booting (fix round 3, item 5).
    emulator_state.note_started(str(avd))
    host()._note_run(str(avd), pid, log_path)
    return pid


# Spawn *avd* detached and return AT ONCE. ``{"error", "content": {"pid"}}``.
#
# ``locale`` is keyword-only and defaults empty, so every existing caller --
# including ``boot``, which passes ``avd`` positionally -- is unchanged.
#
# Extracted from ``boot`` (2026-09-02, Phase 3) because a caller inside an MCP
# tool call cannot afford ``boot``'s poll: it runs to
# ``QA_MOBILE_BOOT_TIMEOUT_S`` (240s by default), which is roughly four times a
# client's tool timeout, so a tester would see a dead editor rather than a
# message. ``boot`` now calls THIS, so there is exactly one place that spawns an
# emulator and the bounded and unbounded paths cannot diverge.
#
# THE KILL-SWITCH LIVES HERE, not on ``boot``: this is the innermost public
# function that spawns, and a guard on a caller is only as good as the list of
# callers. When this function was extracted the guard stayed on ``boot`` and
# this became an unguarded spawn site in the same commit that was meant to close
# that class.
async def start(avd: str, *, locale: str = "") -> dict:
    """Spawn *avd* detached and return AT ONCE."""
    try:
        binary = emulator_state.emulator_binary()
        if not binary:
            return {
                "error": (
                    "The Android emulator binary was not found, so "
                    + str(avd)
                    + " cannot be started. Install Android Studio first."
                ),
                "content": None,
            }
        paths.ensure_tree()
        return {"error": None, "content": {"pid": _launch(avd, binary, locale)}}
    except Exception as exc:
        logger.exception("mobile.emulator.start failed")
        return {"error": str(exc), "content": None}


async def stop(serial: str) -> dict:
    """Ask the emulator to exit (``adb -s <serial> emu kill``)."""
    result = await adb.raw(["-s", str(serial), "emu", "kill"], timeout=30)
    if result.get("error"):
        return result
    return {"error": None, "content": {"serial": str(serial), "stopped": True}}

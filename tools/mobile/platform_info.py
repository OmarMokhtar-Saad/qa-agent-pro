"""Host OS / arch facts the mobile lane needs before it downloads anything.

macOS and native Windows (x86_64) only; Windows on ARM is refused BY NAME.
Every subprocess goes through :func:`run_sync`, one patchable seam.
"""

from __future__ import annotations

import logging
import os  # noqa: F401
import platform
import shutil
import subprocess  # noqa: F401  (patched by tests)
import sys
from typing import Any, cast  # noqa: F401

from tools import host_privileges  # noqa: F401
from tools.mobile.platform_info_fix import (  # noqa: F401
    _ELEVATED_TAIL,
    _HVF_FIX,
    _IT_HANDOFF,
    _UNDETERMINED_TAIL,
    _WHPX_FIX,
    _privilege_suffix,
    hvf_fix,
    whpx_fix,
)
from tools.mobile.platform_info_proc import (
    RunJob,
    detach_kwargs_for,
    detached_devnull,
    os_error_detail,
    powershell_argv,
    reply_tuple,
    safe_which,
)
from tools.mobile.platform_info_table import (  # noqa: F401
    ARM64_ABI,
    BATCH_SUFFIXES,
    CREATE_NEW_PROCESS_GROUP,
    DETACHED_PROCESS,
    DISPLAY,
    MACOS,
    POWERSHELL_TIMEOUT_S,
    SIMULATED,
    SUPPORT,
    TIMEOUT_S,
    VALIDATED,
    WHPX_FEATURE,
    WIN_BATCH_MARKERS,
    WINDOWS,
    X86_64_ABI,
    normalize_arch,
    support_statement,
)
from tools.mobile.platform_info_virt import host_content, macos_verdict, windows_verdict
from tools.process_env import get_env_str  # noqa: F401
from tools.process_runner import (  # noqa: F401  (re-exported; tests read them here)
    CREATE_NO_WINDOW,
    RunOpts,
    no_window_kwargs,
    run,
)

logger = logging.getLogger(__name__)


def is_windows() -> bool:
    """True on native Windows. THE one producer of that fact in this lane."""
    return sys.platform == "win32"


def is_macos() -> bool:
    """True on macOS. Same rule as :func:`is_windows`: one producer, one meaning."""
    return sys.platform == "darwin"


# `is_windows`/`is_macos` answer every question the lane branches on. This
# exists because `windows_probe` has to tell a Windows tester what their
# machine actually calls itself, and a probe that cannot name the host is
# useless. Keeping the read here rather than in the probe is what lets
# `tests/mobile/test_mobile_platform_support.py` keep asserting that exactly
# one module reads `sys.platform`, with no per-file exemption to widen.
def raw_platform() -> str:
    """The host's own platform string, for REPORTING it -- never for branching."""
    return str(sys.platform)


def exe(name: str) -> str:
    """Executable file name for this host: ``adb`` -> ``adb.exe`` on Windows."""
    return name + ".exe" if is_windows() else name


def script(name: str) -> str:
    """cmdline-tools ship ``.bat`` wrappers on Windows (sdkmanager, avdmanager)."""
    return name + ".bat" if is_windows() else name


# POSIX gets its own session so the child outlives the MCP server and never
# receives its terminal signals; Windows gets ``DETACHED_PROCESS`` plus a new
# process group, which is the equivalent, and ``CREATE_NO_WINDOW`` so no
# console flashes in the tester's face.
def detach_kwargs() -> dict:
    """``Popen`` keyword arguments that fully detach a child from this process."""
    return detach_kwargs_for(is_windows())


def detached_devnull_kwargs() -> dict[str, Any]:
    """``Popen`` keyword arguments for a detached child with all three std streams closed.

    A NEW dict on every call, so a caller may add keys to it freely.
    """
    return detached_devnull(detach_kwargs())


def _os_error_detail(cmd: list[str], exc: OSError) -> str:
    """*exc* as a message, NAMING the Windows batch-wrapper case when it is one."""
    return os_error_detail(cmd, exc, is_windows())


# ``stdin_text`` is written to the child's stdin then EOF is sent, so a prompt
# is answered and the child never reads the MCP transport. Returns
# ``(rc, stdout, stderr)``: missing binary 127, overrun 124, other OS error 126.
def run_sync(
    cmd: list[str], timeout: int = TIMEOUT_S, *, stdin_text: str | None = None
) -> tuple[int, str, str]:
    """Run *cmd* with no shell and a mandatory timeout; ``(rc, stdout, stderr)``."""
    r = run(
        cmd,
        timeout=timeout,
        opts=RunOpts(
            no_window=True,
            input=None if stdin_text is None else stdin_text.encode(),
            rc_coerce="int_or_zero",
        ),
    )
    return reply_tuple(r, RunJob(cmd, timeout, _os_error_detail))


def _run_sync(*args: Any, **kwargs: Any) -> tuple[int, str, str]:
    """Private name callers and foreign tests still use; delegates at call time."""
    return run_sync(*args, **kwargs)


# ONE place builds this argv: ``-NoProfile``, ``-NonInteractive``, and the
# interpreter location (PATH, then ``%SystemRoot%``, then the bare name).
def powershell_command(script: str) -> list[str]:
    """The full argv for running *script* through Windows PowerShell."""
    exe_name = exe("powershell")
    found = safe_which(shutil.which, exe_name)
    return powershell_argv(script, found, exe_name)


def host_info() -> dict:
    """``{os, arch, image_abi, emulator_ok, reason}``. Spawns nothing."""
    try:
        plat = sys.platform
        arch = normalize_arch(platform.machine())
        os_name = (
            MACOS if is_macos() else WINDOWS if is_windows() else plat or "unknown"
        )
        return {"error": None, "content": host_content(plat, os_name, arch)}
    except Exception as exc:
        logger.exception("mobile.platform_info.host_info failed")
        return {"error": str(exc), "content": None}


def _macos_virtualization() -> dict:
    reply = run_sync(["sysctl", "-n", "kern.hv_support"])
    return macos_verdict(reply, hvf_fix)


def _windows_virtualization() -> dict:
    script = (
        "(Get-WindowsOptionalFeature -Online -FeatureName " + WHPX_FEATURE + ").State"
    )
    reply = run_sync(powershell_command(script), timeout=POWERSHELL_TIMEOUT_S)
    return windows_verdict(reply, whpx_fix)


def virtualization() -> dict:
    """``{ok, detail, fix}`` -- can this host run an accelerated emulator?"""
    try:
        info = host_info().get("content") or {}
        if not info.get("emulator_ok", False):
            return {
                "error": None,
                "content": {
                    "ok": False,
                    "detail": str(info.get("reason") or "unsupported host"),
                    "fix": "",
                },
            }
        if info.get("os") == "macos":
            return {"error": None, "content": _macos_virtualization()}
        return {"error": None, "content": _windows_virtualization()}
    except Exception as exc:
        logger.exception("mobile.platform_info.virtualization failed")
        return {"error": str(exc), "content": None}

"""Host OS / arch facts the mobile lane needs before it downloads anything.

macOS (Apple Silicon -> ``arm64-v8a``, Intel -> ``x86_64``) and native Windows
(x86_64) only. **Windows on ARM is refused BY NAME**, because Google publishes
no Android emulator build for it -- a generic "unsupported platform" there sends
the tester looking for a setting that does not exist.

Every subprocess in this module goes through :func:`_run_sync`, one seam with a
mandatory timeout and no shell, so tests patch a single name and no test ever
queries the real hypervisor.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

#: Emulator system-image ABIs.
ARM64_ABI = "arm64-v8a"
X86_64_ABI = "x86_64"

#: The Windows optional feature the emulator's WHPX acceleration needs.
WHPX_FEATURE = "HypervisorPlatform"

_HVF_FIX = (
    "This Mac reports no Hypervisor.framework support, so the Android emulator "
    "cannot start with hardware acceleration. Update macOS, and check that no "
    "other hypervisor (a VM product with a kernel extension) holds the CPU "
    "exclusively."
)

_WHPX_FIX = (
    "Enable the Windows Hypervisor Platform and then REBOOT. Open PowerShell "
    "as Administrator and run: Enable-WindowsOptionalFeature -Online "
    "-FeatureName " + WHPX_FEATURE + " -All . Administrator rights are "
    "required for that command and the reboot is not optional. If the feature "
    "already reads Enabled and the emulator still refuses, virtualization is "
    "switched off in the machine firmware (BIOS/UEFI) and must be turned on "
    "there. Once virtualization is on, a NON-administrator account can run the "
    "emulator normally."
)

#: Default timeout for the probes below.
TIMEOUT_S = 20

#: PowerShell is slow to start on a cold profile; give the feature query room.
POWERSHELL_TIMEOUT_S = 60

# Windows process-creation flags. Named here rather than imported from
# subprocess because those attributes do not exist on POSIX, so referencing
# them under a monkeypatched sys.platform would raise instead of exercising the
# branch under test.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _run_sync(cmd: list[str], timeout: int = TIMEOUT_S) -> tuple[int, str, str]:
    """Run *cmd* with no shell and a mandatory timeout.

    Returns ``(rc, stdout, stderr)``. A missing binary reports ``127``, an
    overrun ``124`` and any other OS error ``126`` -- reported, never raised,
    so a probe failure degrades into a preflight line with a fix rather than an
    exception on the MCP boundary.
    """
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
            **no_window_kwargs(),
        )
    except FileNotFoundError:
        return 127, "", "not found: " + (cmd[0] if cmd else "")
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after " + str(timeout) + "s"
    except OSError as exc:
        return 126, "", str(exc)
    out = (proc.stdout or b"").decode(errors="replace")
    err = (proc.stderr or b"").decode(errors="replace")
    return int(proc.returncode or 0), out, err


def normalize_arch(machine: str) -> str:
    """``platform.machine()`` spellings collapsed to ``arm64`` / ``x86_64``."""
    value = (machine or "").strip().lower()
    if value in ("arm64", "aarch64", "armv8b", "armv8l"):
        return "arm64"
    if value in ("x86_64", "amd64", "x64", "em64t"):
        return "x86_64"
    return value or "unknown"


def exe(name: str) -> str:
    """Executable file name for this host: ``adb`` -> ``adb.exe`` on Windows."""
    return name + ".exe" if sys.platform == "win32" else name


def script(name: str) -> str:
    """cmdline-tools ship ``.bat`` wrappers on Windows (sdkmanager, avdmanager)."""
    return name + ".bat" if sys.platform == "win32" else name


def detach_kwargs() -> dict:
    """``Popen`` keyword arguments that fully detach a child from this process.

    POSIX gets its own session so the child outlives the MCP server and never
    receives its terminal signals; Windows gets ``DETACHED_PROCESS`` plus a new
    process group, which is the equivalent, and ``CREATE_NO_WINDOW`` so no
    console flashes in the tester's face.
    """
    if sys.platform == "win32":
        return {
            "creationflags": DETACHED_PROCESS
            | CREATE_NEW_PROCESS_GROUP
            | CREATE_NO_WINDOW
        }
    return {"start_new_session": True}


def no_window_kwargs() -> dict:
    """``Popen`` keywords that keep a SYNCHRONOUS child's console hidden.

    ``detach_kwargs`` already carries ``CREATE_NO_WINDOW`` for the three
    detached spawns, but every SYNCHRONOUS child -- the virtualization probe,
    ``sdkmanager``, ``avdmanager`` and every one of the dozens of ``adb`` calls a
    single case makes -- had no creation flags at all, so on Windows each one
    flashes a console window in the tester's face.

    An empty dict on POSIX rather than ``creationflags=0``: POSIX
    ``subprocess`` tolerates the zero, but ``asyncio.create_subprocess_exec``
    does not accept the keyword at all there, and ``adb`` is an async caller.
    """
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


def powershell_command(script: str) -> list[str]:
    """The full argv for running *script* through Windows PowerShell.

    ONE place builds this argv, because it carries three decisions that must not
    drift: ``-NoProfile`` (a tester's profile can print banners, prompt, or take
    seconds), ``-NonInteractive`` (a probe must never block on a prompt), and the
    interpreter's LOCATION. The last one matters on a locked-down box where
    ``powershell`` is not on ``PATH``: the probe then failed with "not found" and
    the tester was told the hypervisor feature could not be queried for entirely
    the wrong reason. So ``PATH`` is tried first, then the canonical
    ``%SystemRoot%`` location, then the bare name as a last resort.
    """
    exe_name = exe("powershell")
    found = ""
    try:
        found = shutil.which(exe_name) or ""
    except Exception:
        # `Exception`, not `OSError`, and found by RUNNING this rather than
        # reading it: `shutil.which` branches on `sys.platform == "win32"`
        # itself and then touches `_winapi`, which is None on POSIX -- so under
        # a monkeypatched platform it raises `AttributeError`. That is a test
        # artefact rather than a production path, but a resolver that raises is
        # a resolver that cannot be tested here at all, and falling through to
        # the %SystemRoot% lookup is the right answer in both worlds.
        found = ""
    if not found:
        root = (os.environ.get("SystemRoot") or "").strip()
        if root:
            candidate = os.path.join(
                root, "System32", "WindowsPowerShell", "v1.0", exe_name
            )
            try:
                if os.path.isfile(candidate):
                    found = candidate
            except OSError:  # pragma: no cover - defensive
                found = ""
    return [
        found or "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        str(script),
    ]


def host_info() -> dict:
    """``{os, arch, image_abi, emulator_ok, reason}``. Spawns nothing."""
    try:
        plat = sys.platform
        arch = normalize_arch(platform.machine())
        if plat == "darwin":
            os_name = "macos"
        elif plat == "win32":
            os_name = "windows"
        else:
            os_name = plat or "unknown"
        image_abi = ARM64_ABI if arch == "arm64" else X86_64_ABI
        emulator_ok = True
        reason = ""
        if os_name == "windows" and arch == "arm64":
            emulator_ok = False
            reason = (
                "Windows on ARM is not supported by the mobile lane: Google "
                "publishes no Android emulator build for it, so there is "
                "nothing to download. Use an x86_64 Windows machine or a Mac."
            )
        elif os_name not in ("macos", "windows"):
            emulator_ok = False
            reason = (
                "The mobile lane supports macOS and native Windows only; this "
                "host reports sys.platform=" + str(plat) + "."
            )
        return {
            "error": None,
            "content": {
                "os": os_name,
                "arch": arch,
                "image_abi": image_abi,
                "emulator_ok": emulator_ok,
                "reason": reason,
            },
        }
    except Exception as exc:
        logger.exception("mobile.platform_info.host_info failed")
        return {"error": str(exc), "content": None}


def _macos_virtualization() -> dict:
    rc, out, err = _run_sync(["sysctl", "-n", "kern.hv_support"])
    if rc != 0:
        detail = (err.strip() or "rc=" + str(rc))[:200]
        return {
            "ok": False,
            "detail": "could not query kern.hv_support (" + detail + ")",
            "fix": _HVF_FIX,
        }
    value = out.strip()
    ok = value == "1"
    return {
        "ok": ok,
        "detail": "kern.hv_support=" + (value or "?"),
        "fix": "" if ok else _HVF_FIX,
    }


def _windows_virtualization() -> dict:
    rc, out, err = _run_sync(
        powershell_command(
            "(Get-WindowsOptionalFeature -Online -FeatureName "
            + WHPX_FEATURE
            + ").State"
        ),
        timeout=POWERSHELL_TIMEOUT_S,
    )
    if rc != 0:
        detail = (err.strip() or "rc=" + str(rc))[:200]
        return {
            "ok": False,
            "detail": (
                "could not query the "
                + WHPX_FEATURE
                + " feature ("
                + detail
                + "). Get-WindowsOptionalFeature itself needs an elevated "
                "shell, so this is NOT proof the feature is off."
            ),
            "fix": _WHPX_FIX,
        }
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    state = lines[-1] if lines else ""
    ok = state.lower() == "enabled"
    return {
        "ok": ok,
        "detail": WHPX_FEATURE + " state=" + (state or "?"),
        "fix": "" if ok else _WHPX_FIX,
    }


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

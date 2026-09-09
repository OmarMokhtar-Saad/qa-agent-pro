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

from tools import host_privileges

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

# The privilege-aware half of the two fix texts above. The BASE constants stay
# exactly as they are -- they carry the facts that are true either way, and
# tests bind to those names -- and a suffix is appended per verdict. One
# producer for the verdict (tools.host_privileges, top level so it is reachable
# on an edition with no tools/mobile on disk); this module only renders it.
#
# The fact `_WHPX_FIX` already states and which MUST survive: once
# virtualization is on, a NON-administrator account runs the emulator normally.
# Without it, a tester who cannot elevate reads the IT handoff as "the emulator
# needs admin", which is false and would stop them using the lane at all.
_ELEVATED_TAIL = (
    " This account can elevate on this machine, so run the command above "
    "yourself in an elevated shell."
)

_IT_HANDOFF = (
    " This account cannot elevate on this machine, so do NOT keep retrying the "
    "command above -- send your IT desk exactly this: enable the feature named "
    "above and reboot. Group Policy can deny elevation even to a member of the "
    "Administrators group, so being listed there is not enough. Once "
    "virtualization is on, a NON-administrator account runs the emulator "
    "normally, and nothing else in this lane needs administrator rights."
)

_UNDETERMINED_TAIL = (
    " Whether this account can elevate could not be determined -- that is "
    "UNDETERMINED, not a lack of administrator rights. Try the command above; "
    "if it is refused, send your IT desk the same request. Once virtualization "
    "is on, a NON-administrator account runs the emulator normally."
)


def _privilege_suffix(names_a_privileged_step: bool) -> str:
    """The one clause that turns a generic instruction into an actionable one.

    The parameter has NO DEFAULT, deliberately. A default is how a bound clause
    gets silenced at a call site nobody re-reads: defaulting to True is the
    Windows shape, so a future caller whose base text names no command would
    inherit the exact defect described below without writing a line about it.
    Every call site must say which kind of base text it is appending to.

    An ``error`` or missing verdict yields the UNDETERMINED tail, never the IT
    handoff: telling a tester who can elevate to go bother IT is the failure
    this whole change exists to remove, and a failed probe is not evidence.

    THE TAIL MAY NOT NAME A REMEDY THE BASE TEXT DOES NOT CONTAIN. All three
    tails were written against the Windows base: they say "the command above",
    "the feature named above", and warn that Group Policy can deny elevation to
    a member of the Administrators group. ``_HVF_FIX`` contains no command, no
    feature and no PowerShell, and macOS has neither Group Policy nor an
    Administrators group -- so a Mac tester with no HVF support and a
    locked-down account was told to stop retrying a command that was never
    printed and to ask IT to enable a feature that does not exist. Worse, it
    contradicted the correct remedy one sentence earlier, because
    ``host_privileges`` states that Hypervisor.framework needs no enabling step
    and no admin rights at all.

    So the caller declares whether its base names a privileged step, and when
    it does not there is NO TAIL: if nothing here requires elevation, the
    account's elevation state cannot change what the tester should do, and any
    sentence about it is noise at best and a wrong instruction at worst.
    """
    if not names_a_privileged_step:
        return ""
    try:
        content = (host_privileges.probe() or {}).get("content") or {}
    except Exception:
        logger.debug("privilege suffix unavailable", exc_info=True)
        return _UNDETERMINED_TAIL
    if content.get("undetermined") or not content:
        return _UNDETERMINED_TAIL
    if content.get("elevated") or content.get("can_elevate"):
        return _ELEVATED_TAIL
    return _IT_HANDOFF


def whpx_fix() -> str:
    """``_WHPX_FIX`` plus what THIS account can actually do about it."""
    return _WHPX_FIX + _privilege_suffix(names_a_privileged_step=True)


def hvf_fix() -> str:  # noqa: D401 - see _privilege_suffix
    """``_HVF_FIX`` plus what THIS account can actually do about it."""
    # NO PRIVILEGE TAIL. `_HVF_FIX` names no command and no feature, and
    # Hypervisor.framework needs neither an enabling step nor admin rights --
    # see `_privilege_suffix`. Every tail would describe a remedy this text
    # does not contain.
    return _HVF_FIX + _privilege_suffix(names_a_privileged_step=False)


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

# --- THE PLATFORM-SUPPORT TABLE: ONE PRODUCER --------------------------------
#
# Every "is this Windows?" in the mobile lane resolves HERE. Five modules used to
# spell it themselves -- this one, `paths` and `sdk_locator` with
# `== "win32"`, `open_report` with `.startswith("win")`, and
# `emulator.python_is_windows`, which had no production caller at all. Five
# derivations of one boolean in two different spellings is the mirrored-condition
# drift class, and the copy that differed sat in the one module whose Windows
# branch (`os.startfile`) has never executed.
#
# `tests/mobile/test_mobile_platform_support.py` asserts `sys.platform` is read
# in no other module under `tools/mobile/`, so a new call site fails BY FILE NAME
# rather than joining the drift.

MACOS = "macos"
WINDOWS = "windows"

#: Tester-facing spelling of each os name. The internal keys stay lowercase
#: because `host_info` has always returned them that way and callers branch on
#: them; only prose uses these.
DISPLAY: dict[str, str] = {MACOS: "macOS", WINDOWS: "Windows"}

#: The EVIDENCE vocabulary. Two values, no third: either a real machine has run
#: the lane end to end or it has not. "Partly" is the answer that let the Windows
#: claim drift across four documents for a whole phase.
VALIDATED = "validated-on-hardware"
SIMULATED = "simulated-only"

#: os name -> evidence. THE ONLY PLACE the project states what has actually been
#: exercised. :func:`support_statement` DERIVES the disclosure sentence from it,
#: and that sentence must appear verbatim in CLAUDE.md, docs/MOBILE_TESTING.md,
#: tools/mobile/__init__.py and scripts/build_dist.py -- so flipping a value here
#: turns four documents red instead of leaving them quietly wrong.
SUPPORT: dict[str, str] = {
    MACOS: VALIDATED,
    WINDOWS: SIMULATED,
}

#: What Windows raises when CreateProcess is handed a file that is not an
#: executable image -- which is what a `.bat` wrapper is. `sdkmanager` and
#: `avdmanager` ARE `.bat` files there (`sdk_locator.SCRIPT_TOOLS`), and they are
#: invoked as a bare argv with no shell, so this is the most likely first failure
#: of a real Windows provisioning run. It is UNPROVEN either way: Microsoft
#: documents CreateProcess as needing `cmd.exe /c` for a batch file, and no
#: machine here can settle it.
#:
#: IT IS NOT SILENTLY WORKED AROUND. Wrapping the argv in `cmd.exe /c` would hand
#: the tester's SDK path -- which may legally contain `&`, `^` and `%` -- to cmd's
#: own parser, and `subprocess.list2cmdline` does not escape those. A guess would
#: trade an unproven failure for an unproven INJECTION. So the failure is NAMED
#: instead: which binary, which cause, and that the path is unproven and should be
#: reported rather than patched around.
WIN_BATCH_MARKERS: tuple[str, ...] = ("winerror 193", "not a valid win32 application")
BATCH_SUFFIXES: tuple[str, ...] = (".bat", ".cmd")


def is_windows() -> bool:
    """True on native Windows. THE one producer of that fact in this lane."""
    return sys.platform == "win32"


def is_macos() -> bool:
    """True on macOS. Same rule as :func:`is_windows`: one producer, one meaning."""
    return sys.platform == "darwin"


def raw_platform() -> str:
    """The host's own platform string, for REPORTING it -- never for branching.

    `is_windows`/`is_macos` answer every question the lane branches on. This
    exists because `windows_probe` has to tell a Windows tester what their
    machine actually calls itself, and a probe that cannot name the host is
    useless. Keeping the read here rather than in the probe is what lets
    `tests/mobile/test_mobile_platform_support.py` keep asserting that exactly
    one module reads `sys.platform`, with no per-file exemption to widen.
    """
    return str(sys.platform)


def support_statement(os_name: str = WINDOWS) -> str:
    """The one-sentence support disclosure for *os_name*, DERIVED from :data:`SUPPORT`.

    Derived, never written twice. A pin greps the returned sentence out of every
    document that carries the claim, so promoting Windows to :data:`VALIDATED`
    without a real run leaves four documents holding a sentence this function no
    longer produces -- and the pin names each one. That is what makes the claim
    checkable in BOTH directions instead of being prose somebody has to remember
    to update.
    """
    name = str(os_name or "")
    shown = DISPLAY.get(name, name)
    evidence = SUPPORT.get(name, "")
    if evidence == VALIDATED:
        return "The mobile lane has been run end to end on " + shown + " hardware."
    if evidence == SIMULATED:
        return (
            "The mobile lane's "
            + shown
            + " branches are exercised only by tests that fake the platform; no "
            + shown
            + " machine has run the lane."
        )
    return "The mobile lane does not support " + (shown or "that host") + "."


def _os_error_detail(cmd: list[str], exc: OSError) -> str:
    """*exc* as a message, NAMING the Windows batch-wrapper case when it is one.

    Three clauses, and each is graded by a fixture where only IT can reject the
    impostor: the platform, the `.bat` suffix, and the Win32 marker text. A
    non-batch OSError on Windows and a batch-shaped OSError on POSIX both fall
    through to the raw string, because dressing up an error we have not
    identified is how a wrong diagnosis gets believed.
    """
    raw = str(exc)
    binary = str(cmd[0]) if cmd else ""
    lowered = (raw + " " + str(getattr(exc, "strerror", "") or "")).lower()
    looks_like_batch = binary.lower().endswith(BATCH_SUFFIXES)
    marked = any(marker in lowered for marker in WIN_BATCH_MARKERS)
    if is_windows() and looks_like_batch and marked:
        return (
            "Windows refused to run "
            + binary
            + " directly: a .bat wrapper is not an executable image, so "
            "CreateProcess rejected it ("
            + raw
            + "). THIS PATH IS UNPROVEN -- no Windows machine has run this lane. "
            "Please report this line together with the full path above, and do "
            "not work around it by editing the file. To tell our invocation "
            "apart from a broken SDK, run the same command yourself in a Command "
            "Prompt: if it works there, the fault is ours."
        )
    return raw


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
        return 126, "", _os_error_detail(cmd, exc)
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
    return name + ".exe" if is_windows() else name


def script(name: str) -> str:
    """cmdline-tools ship ``.bat`` wrappers on Windows (sdkmanager, avdmanager)."""
    return name + ".bat" if is_windows() else name


def detach_kwargs() -> dict:
    """``Popen`` keyword arguments that fully detach a child from this process.

    POSIX gets its own session so the child outlives the MCP server and never
    receives its terminal signals; Windows gets ``DETACHED_PROCESS`` plus a new
    process group, which is the equivalent, and ``CREATE_NO_WINDOW`` so no
    console flashes in the tester's face.
    """
    if is_windows():
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
    if is_windows():
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
        if is_macos():
            os_name = MACOS
        elif is_windows():
            os_name = WINDOWS
        else:
            os_name = plat or "unknown"
        image_abi = ARM64_ABI if arch == "arm64" else X86_64_ABI
        emulator_ok = True
        reason = ""
        # The ARCH clause stays an EXPLICIT refusal rather than becoming
        # `arch in SUPPORT[os_name]["arches"]`. A table-driven arch test would
        # newly refuse a host whose `normalize_arch` answered "unknown", which
        # proceeds today -- a behaviour change with no Windows evidence behind
        # it. OS membership is derived (below); the arch decision is not.
        if os_name == WINDOWS and arch == "arm64":
            emulator_ok = False
            reason = (
                "Windows on ARM is not supported by the mobile lane: Google "
                "publishes no Android emulator build for it, so there is "
                "nothing to download. Use an x86_64 Windows machine or a Mac."
            )
        elif os_name not in SUPPORT:
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
            "fix": hvf_fix(),
        }
    value = out.strip()
    ok = value == "1"
    return {
        "ok": ok,
        "detail": "kern.hv_support=" + (value or "?"),
        "fix": "" if ok else hvf_fix(),
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
            "fix": whpx_fix(),
        }
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    state = lines[-1] if lines else ""
    ok = state.lower() == "enabled"
    return {
        "ok": ok,
        "detail": WHPX_FEATURE + " state=" + (state or "?"),
        "fix": "" if ok else whpx_fix(),
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

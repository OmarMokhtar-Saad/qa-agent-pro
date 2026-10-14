"""The OS support table, constants and arch/ABI helpers of platform_info (split out)."""

from __future__ import annotations

#: Emulator system-image ABIs.
ARM64_ABI = "arm64-v8a"
X86_64_ABI = "x86_64"


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


# Derived, never written twice. A pin greps the returned sentence out of every
# document that carries the claim, so promoting Windows to :data:`VALIDATED`
# without a real run leaves four documents holding a sentence this function no
# longer produces -- and the pin names each one. That is what makes the claim
# checkable in BOTH directions instead of being prose somebody has to remember
# to update.
def support_statement(os_name: str = WINDOWS) -> str:
    """The one-sentence support disclosure for *os_name*, DERIVED from :data:`SUPPORT`."""
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


def normalize_arch(machine: str) -> str:
    """``platform.machine()`` spellings collapsed to ``arm64`` / ``x86_64``."""
    value = (machine or "").strip().lower()
    if value in ("arm64", "aarch64", "armv8b", "armv8l"):
        return "arm64"
    if value in ("x86_64", "amd64", "x64", "em64t"):
        return "x86_64"
    return value or "unknown"


WHPX_FEATURE = "HypervisorPlatform"

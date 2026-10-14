"""Fix texts and the privilege-aware tails of platform_info (split out)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools import host_privileges
from tools.mobile.platform_info_table import WHPX_FEATURE

logger = logging.getLogger("tools.mobile.platform_info")

#: The Windows optional feature the emulator's WHPX acceleration needs.

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


# The parameter has NO DEFAULT, deliberately. A default is how a bound clause
# gets silenced at a call site nobody re-reads: defaulting to True is the
# Windows shape, so a future caller whose base text names no command would
# inherit the exact defect described below without writing a line about it.
# Every call site must say which kind of base text it is appending to.
#
# An ``error`` or missing verdict yields the UNDETERMINED tail, never the IT
# handoff: telling a tester who can elevate to go bother IT is the failure
# this whole change exists to remove, and a failed probe is not evidence.
#
# THE TAIL MAY NOT NAME A REMEDY THE BASE TEXT DOES NOT CONTAIN. All three
# tails were written against the Windows base: they say "the command above",
# "the feature named above", and warn that Group Policy can deny elevation to
# a member of the Administrators group. ``_HVF_FIX`` contains no command, no
# feature and no PowerShell, and macOS has neither Group Policy nor an
# Administrators group -- so a Mac tester with no HVF support and a
# locked-down account was told to stop retrying a command that was never
# printed and to ask IT to enable a feature that does not exist. Worse, it
# contradicted the correct remedy one sentence earlier, because
# ``host_privileges`` states that Hypervisor.framework needs no enabling step
# and no admin rights at all.
#
# So the caller declares whether its base names a privileged step, and when
# it does not there is NO TAIL: if nothing here requires elevation, the
# account's elevation state cannot change what the tester should do, and any
# sentence about it is noise at best and a wrong instruction at worst.
def _privilege_suffix(names_a_privileged_step: bool) -> str:
    """The one clause that turns a generic instruction into an actionable one."""
    if not names_a_privileged_step:
        return ""
    with never_raise(logger, "privilege suffix unavailable", exc_info=True) as g:
        content = (host_privileges.probe() or {}).get("content") or {}
    if g.failed:
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

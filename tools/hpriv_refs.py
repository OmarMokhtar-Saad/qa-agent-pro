"""Official vendor URLs for the host-privilege advice (one producer)."""

from __future__ import annotations

#: ONE producer for the official vendor URLs, so a stale link is fixed in one
#: place -- and the ONLY place a URL lives in this module. Every value here was
#: sourced by research and GRADED before it was pinned; each is a vendor page
#: rather than a direct binary link, because a binary link rots faster and a
#: tester cannot check it. A test asserts every value matches ``^https://``,
#: which is why prose that legitimately carries no URL is kept OUT of this
#: mapping instead of being given an invented one.
#:
#: THREE CLAIMS THIS MODULE MAKES WITH NO URL, DELIBERATELY:
#:   * macOS Hypervisor.framework needs no enabling step and no admin rights --
#:     it is present or it is not. The only source found was an Apple developer
#:     FORUM thread, which is not a vendor doc, so the fact is stated plainly
#:     and ``emulator_accel`` covers the emulator's side of it.
#:   * ``Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform``
#:     has no single canonical Microsoft page. The command text stays (it is
#:     correct); ``win_policy_lockdown`` covers the "IT may have blocked this
#:     even for admins" case. No URL is invented for the cmdlet.
#:   * BIOS/UEFI firmware virtualization has no dedicated official page. The
#:     existing factual sentence stands without a citation.
REFERENCES: dict[str, str] = {
    # Command line tools: unzip into a user directory and run
    # sdkmanager/avdmanager.
    #
    # PROVENANCE, because the distinction decides whether we may say it: the
    # page states that a "command line tools only" ZIP exists and is usable
    # without installing Android Studio -- VERIFIED. It does NOT contain the
    # words "no administrator rights"; that property is OUR inference from the
    # artifact's form, since unzipping into a directory the tester already owns
    # requires no elevation by construction. So the no-admin claim is ours to
    # defend and is phrased as what the tester DOES ("unzip it into your home
    # directory"), never as "Google says you need no admin".
    "cmdline_tools_zip": "https://developer.android.com/studio",
    # platform-tools (adb) is a plain zip -- verified on the page. The landing
    # page rather than the dl.google.com direct link, which rots and cannot be
    # eyeballed. Same provenance split as above: "plain zip" is the page's,
    # "therefore no admin" is ours.
    "platform_tools_zip": "https://developer.android.com/tools/releases/platform-tools",
    "emulator_accel": "https://developer.android.com/studio/run/emulator-acceleration",
    # `--scope` with values `user` or `machine` is documented HERE, on the
    # install-command page -- NOT on the winget landing page, which was cited
    # for it until the page was actually read and does not mention the option
    # at all.
    "winget_scope": "https://learn.microsoft.com/en-us/windows/package-manager/winget/install",
    # And this is the page that says what `--scope user` does NOT do. Microsoft
    # is explicit: "Packages installed in user scope may still require UAC
    # (User Account Control) authorization from an administrator." So scope
    # selects a LOCATION, not an elevation bypass. The first draft of the step
    # text below claimed `--scope user` "needs no elevation for the installer"
    # -- a false promise that would have sent a locked-down tester into a UAC
    # prompt they cannot satisfy, which is the exact failure this module was
    # written to remove. The zip route is the one that cannot prompt.
    "winget_scope_uac": (
        "https://learn.microsoft.com/en-us/windows/package-manager/winget/"
        "troubleshooting"
    ),
    # Homebrew's own install needs sudo and documents no supported non-admin
    # alternative; `brew install` for a regular formula afterwards does not.
    # The key is named for what the page IS, not for a user-prefix route that
    # Homebrew does not support.
    "brew_install": "https://docs.brew.sh/Installation",
    # The sentence the IT-handoff line rests on: policy can deny virtualization
    # features per machine, Administrators-group membership notwithstanding.
    "win_policy_lockdown": (
        "https://learn.microsoft.com/en-us/troubleshoot/windows-server/"
        "virtualization/block-users-from-running-virtualization-features-"
        "on-specific-computers"
    ),
    # Where the accelerator's status is stated, and the page an implementer
    # must re-read before any date reaches tester-facing text.
    #
    # Verified against the page itself, which CORRECTED what research had
    # claimed -- the reason the re-read is mandatory rather than advisory:
    #   * HAXM is REMOVED, not merely deprecated: "HAXM support is removed from
    #     the Emulator" (v36.2.11, 2025-10-09). Research had said "deprecated
    #     Jan 2023"; the page states no such date, so that claim was dropped.
    #   * AEHD has an announced sunset: "The Android Emulator hypervisor driver
    #     (AEHD) will sunset on December 31, 2026" (v37.1.11, 2026-07-30).
    #     Research's date was right; its provenance is now first-hand.
    #
    # NEITHER DATE IS PRINTED FOR A TESTER. The safe tester-facing form is
    # "use WHPX" with no date and no version, because a wrong date in install
    # advice is precisely the failure this module exists to prevent -- and one
    # of the two dates WAS wrong until the page was read.
    "emulator_release_notes": "https://developer.android.com/studio/releases/emulator",
}

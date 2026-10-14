"""Every install/provision step the server can propose (admin-free route first)."""

from __future__ import annotations

from tools.hpriv_refs import REFERENCES

#: Every install/provision step this server can propose, with the ADMIN-FREE
#: route named FIRST. ``needs_admin`` is a property of the STEP; whether it is
#: attemptable is a property of the step AND the verdict, which is why the two
#: live apart and ``attemptability`` joins them.
#:
#: ``needs_network`` is a SECOND precondition, kept separate for the same
#: reason. This module probes PRIVILEGE and nothing else, so "your account may
#: attempt this" is the only claim it can make. A step that fetches something
#: also needs the fetch to succeed, and on a corporate machine it often does
#: not: an authenticating proxy or a TLS-inspecting middlebox refuses or
#: rewrites the download, and neither is a privilege problem. Saying only
#: "attemptable: yes" to a tester in that position sends them to retry a route
#: that cannot work and tells them nothing about why -- the same shape as
#: naming a remedy the base text does not contain.
STEPS: tuple[dict, ...] = (
    {
        "step": "install adb (platform-tools)",
        "needs_admin": False,
        "needs_network": True,
        "admin_free_route": (
            "Unpack the platform-tools zip into your home directory and add it "
            "to PATH. No administrator rights are involved: "
            + REFERENCES["platform_tools_zip"]
        ),
    },
    {
        "step": "install the Android SDK (cmdline-tools)",
        "needs_admin": False,
        "needs_network": True,
        "admin_free_route": (
            "Unpack the cmdline-tools zip under your own home directory and "
            "point ANDROID_HOME at it; this server downloads no SDK itself: "
            + REFERENCES["cmdline_tools_zip"]
        ),
    },
    {
        "step": "install via a package manager (winget / brew)",
        "needs_admin": False,
        "needs_network": True,
        "admin_free_route": (
            "On Windows, winget itself runs fine as a standard user, and "
            "winget install --scope user chooses a per-user install LOCATION "
            "-- but it is NOT an elevation bypass: Microsoft documents that a "
            "user-scope package may still ask for administrator approval, and "
            "if you decline, the install fails. So try it if you like, and if "
            "it asks to elevate and you cannot, use the zip route above, "
            "which never prompts. Option: "
            + REFERENCES["winget_scope"]
            + " ; the elevation caveat: "
            + REFERENCES["winget_scope_uac"]
            + ". On macOS, INSTALLING Homebrew itself needs an administrator "
            "(there is no supported non-admin route), but brew install for a "
            "regular formula afterwards does not -- so if Homebrew is already "
            "there, this step needs nobody: " + REFERENCES["brew_install"]
        ),
    },
    {
        "step": "enable hardware virtualization (Windows HypervisorPlatform)",
        # THE ONLY OS-SPECIFIC STEP. Without this key it was joined to every
        # verdict, so a macOS tester read "enable the Windows
        # HypervisorPlatform -- attemptable: unknown. Hand IT this step" two
        # bullets above another saying macOS has nothing to enable. "unknown"
        # also OVERSTATED it: the step is inapplicable here, not undetermined.
        "os": "windows",
        "needs_admin": True,
        "admin_free_route": (
            "There is no admin-free route for this one: the feature switch "
            "(Enable-WindowsOptionalFeature -Online -FeatureName "
            "HypervisorPlatform, in an ELEVATED PowerShell) and the reboot "
            "both need administrator rights, and Group Policy can deny them "
            "even to a member of the Administrators group. Hand IT this step. "
            "Once virtualization is on, a NON-administrator account runs the "
            "emulator normally: "
            + REFERENCES["win_policy_lockdown"]
            + " and "
            + REFERENCES["emulator_accel"]
        ),
    },
    {
        "step": "create an AVD and launch the emulator",
        "needs_admin": False,
        "admin_free_route": (
            "Runs entirely inside your home directory once virtualization "
            "is on. On macOS there is nothing to enable and no administrator "
            "involved at all: Hypervisor.framework is either present on the "
            "machine or it is not, with no enabling step and no rights to "
            "grant -- stated as fact here because the only source found for "
            "it was a developer forum thread, which is not a vendor doc: "
            + REFERENCES["emulator_accel"]
        ),
    },
)

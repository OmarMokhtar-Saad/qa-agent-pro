"""Per-tool facts for the host-check report: what each binary is FOR and how to install it."""

from __future__ import annotations

# What each optional binary is FOR, and how to get it per platform. A row with no
# command for this platform prints no command -- offering `brew` on Windows is
# worse than offering nothing, and `xcrun` cannot exist off macOS at all.
_TOOL_INFO: dict[str, dict] = {
    "adb": {
        # NAMED FIRST in the rendered row, deliberately: an account that cannot
        # elevate has no use for the package-manager line, and a tester reads
        # the first option. Prose, because it is a route rather than a command
        # -- and therefore outside the backticks.
        "admin_free": {
            "win32": "Unpack Google's platform-tools into your home directory "
            "and add it to PATH (no administrator rights)",
            "darwin": "Unpack Google's platform-tools into your home directory "
            "and add it to PATH (no administrator rights)",
        },
        # Deliberately NOT naming `qa_list_devices` here: a guard test asserts
        # that string never appears in the setup report, because the report must
        # not read as though it had enumerated the tester's devices.
        "purpose": "Android device listing and screen capture",
        "install": {
            # ADMIN-FREE ROUTE FIRST, then the package manager, then the
            # official URL -- and the URL comes from tools.host_privileges so a
            # stale link is fixed in ONE place. `--scope user` is what makes
            # the winget line runnable on a locked-down corporate laptop; the
            # literal prefix before it is pinned by a test, so it stays.
            # A RUNNABLE COMMAND ONLY. `_binary_line` renders this inside
            # backticks after the word "Install:", and its docstring promises
            # the install command for THIS platform -- so a non-technical
            # tester copy-pastes whatever is here. This carried a prose
            # paragraph with two alternatives and a URL, which pastes into a
            # shell error. The admin-free unzip route is not lost: it is the
            # `admin_free_route` of the platform-tools step in
            # `host_privileges.STEPS`, which is where a route rather than a
            # command belongs. Linux has no entry at all and degrades to a row
            # with no command clause; this now matches that shape.
            "win32": "winget install --id Google.PlatformTools -e --scope user",
            "darwin": "brew install --cask android-platform-tools",
            # No `linux` row, and its absence is the honest answer rather than
            # an omission: the hosts in scope are macOS and Windows, mirroring
            # tools/mobile/platform_info.SUPPORT. The deleted row read
            # `sudo apt install android-tools-adb` -- an elevated command, for
            # an unsupported host, handed to every tester regardless of
            # whether they could run it. `_binary_line` prints no command when
            # a platform has no row, which is what should happen here.
        },
    },
    "xcrun": {
        "purpose": "iOS Simulator, macOS only — ships with Xcode",
        "install": {"darwin": "xcode-select --install"},
    },
}

"""Package installer dialog: Install, then Update, then Done, then Open.

The installer belongs to another app, so a step that installs or updates an app
used to be cut at the first installer screen. This handler taps the one control
that moves the install forward. It never taps Uninstall: that control is not in
its list, the central denylist would refuse it anyway, and
:class:`~tools.mobile.dialog_defaults.ChoiceHandler` skips any screen that
mentions an uninstall.

Package ids are the stock Google, AOSP and Samsung installers. A vendor installer
not listed here is not handled and the executor refuses as before.
"""

from __future__ import annotations

from tools.mobile import dialog_handlers as dh
from tools.mobile.dialog_defaults import ChoiceHandler

INSTALLER_PACKAGES = frozenset(
    {
        "com.google.android.packageinstaller",
        "com.android.packageinstaller",
        "com.samsung.android.packageinstaller",
    }
)

#: Best first. Exact visible text, so "Install anyway" is NOT "Install".
INSTALL_BUTTONS = ("Install", "Update", "Done", "Open")

HANDLER = ChoiceHandler(
    name="package_installer",
    packages=INSTALLER_PACKAGES,
    choices=tuple(
        dh.DialogAction(dh.TAP_TEXT, text, "package installer: " + text)
        for text in INSTALL_BUTTONS
    ),
)

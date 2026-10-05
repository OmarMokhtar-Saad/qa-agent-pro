"""Runtime permission dialog: \"While using the app\", then exactly \"Allow\".

A step that needs a permission used to be cut at the system dialog. The handler
taps the narrowest grant on offer. It never taps \"Allow all the time\" (widening a
grant is the tester's call; the central denylist refuses it as well) and never
taps \"Don't allow\" -- declining is not its decision to make. Matching is on the
EXACT visible text, so \"Allow all the time\" is not \"Allow\".

The legacy ``com.android.packageinstaller`` package also hosted permission
dialogs before Android 10, so it is listed here as well; the installer handler
decides nothing on such a screen and the next handler is tried.
"""

from __future__ import annotations

from tools.mobile import dialog_handlers as dh
from tools.mobile.dialog_defaults import ChoiceHandler

PERMISSION_PACKAGES = frozenset(
    {
        "com.google.android.permissioncontroller",
        "com.android.permissioncontroller",
        "com.android.packageinstaller",
    }
)

#: Best first.
PERMISSION_BUTTONS = ("While using the app", "Allow")

HANDLER = ChoiceHandler(
    name="permission_controller",
    packages=PERMISSION_PACKAGES,
    choices=tuple(
        dh.DialogAction(dh.TAP_TEXT, text, "permission: " + text)
        for text in PERMISSION_BUTTONS
    ),
)

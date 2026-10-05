"""Human labels for installed packages, so "App Tester" finds its package.

``pm list packages`` returns package ids only, and stock adb has no shell
command that prints an app's launcher label (that needs aapt on the host or a
helper on the device). So labels come from a pluggable :data:`LabelLister`; the
default knows the system and distribution apps a mobile run touches by name.
A device-side lister (for example through the opt-in uiautomator2 helper) can
plug in later without touching :mod:`tools.mobile.app_pick`.

A label only widens matching: resolve-or-ask still asks when several packages
match, and a label equal to the text is never an exact match.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Sequence

#: ``(serial, packages) -> {package: label}`` for the packages it knows.
LabelLister = Callable[[str, Sequence[str]], Awaitable[dict]]

KNOWN_LABELS = {
    "dev.firebase.appdistribution": "App Tester",
    "com.android.vending": "Play Store",
    "com.android.settings": "Settings",
    "com.android.chrome": "Chrome",
}


async def known_labels(serial: str, packages: Sequence[str]) -> dict:
    """The built-in labels for whichever of ``packages`` are installed."""
    del serial  # the built-in table is the same on every device
    return {p: KNOWN_LABELS[p] for p in packages if p in KNOWN_LABELS}

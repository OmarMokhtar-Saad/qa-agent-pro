"""Replay: the system installer's "Installing..." screen is a wait state, not a stop.

Measured (``mrun-20261021-163646-1b4628`` / ``-163958-25dc86``): the same Android
package-installer screen ended one run as ``failed system_dialog`` (the settle
path) and the other as ``abandoned`` (the wait path), though the install finished
both times. While the installer shows only "Installing..." there is nothing to
answer: the replay waits for that text to go, on both paths.
"""

from __future__ import annotations

import re

from tools.mobile.replay.screen import system_dialog_package

_INSTALLERS = ("packageinstaller",)
_INSTALLING = re.compile(r"^installing\b", re.I)


def _labels(screen: dict) -> list[str]:
    """Every non-empty text and description on *screen*."""
    found = []
    for row in screen.get("elements") or []:
        for field in ("text", "desc"):
            value = " ".join(str((row or {}).get(field) or "").split())
            if value:
                found.append(value)
    return found


def installing_text(screen: object) -> str:
    """The "Installing..." label when *screen* is a system installer mid-install, else ``""``."""
    package = system_dialog_package(screen)
    if not package or not any(name in package for name in _INSTALLERS):
        return ""
    for label in _labels(screen) if isinstance(screen, dict) else []:
        if _INSTALLING.match(label):
            return label
    return ""

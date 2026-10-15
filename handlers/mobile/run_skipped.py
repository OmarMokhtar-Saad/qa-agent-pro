"""The note for an install the app stage skipped because the app is installed."""

from __future__ import annotations

from tools.mobile import imes_upgrade

_SKIPPED: dict[str, tuple[str, str]] = {}


def mark_skipped(serial: str, target: str, asked: str) -> None:
    """Record that the *asked* install of *target* was skipped on *serial*."""
    _SKIPPED[serial] = (target, asked)


def reset() -> None:
    """Forget every mark (tests)."""
    _SKIPPED.clear()


async def take_note(serial: str) -> str:
    """Markdown for the skipped install on *serial* (once), or ``""``."""
    target, asked = _SKIPPED.pop(serial, ("", ""))
    if not target:
        return ""
    version = await imes_upgrade.installed_version(serial, target)
    have = "already installed" + (" v" + version if version else "")
    return (
        "> Skipped: install of `"
        + target
        + "` (source `"
        + asked
        + "`), "
        + have
        + ". Ask the user whether they want a reinstall or update; "
        "do not reinstall on your own.\n\n"
    )

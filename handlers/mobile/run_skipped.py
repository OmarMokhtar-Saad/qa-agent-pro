"""Notes for the start reply about install steps the server already did or skipped."""

from __future__ import annotations

from tools.mobile import imes_upgrade

_SKIPPED: dict[str, tuple[str, str]] = {}
_TESTER: dict[str, tuple[str, bool]] = {}
_LAUNCH: dict[str, str] = {}


def mark_skipped(serial: str, target: str, asked: str) -> None:
    """Record that the *asked* install of *target* was skipped on *serial*."""
    _SKIPPED[serial] = (target, asked)


def mark_tester_install(serial: str, package: str, fresh: bool) -> None:
    """Record that the App Tester stage ran for *package* (*fresh*: downloaded now)."""
    _TESTER[serial] = (package, fresh)


def tester_ran(serial: str) -> bool:
    """Whether the App Tester install stage ran for *serial* in this start."""
    return serial in _TESTER


def mark_launch(serial: str, state: str) -> None:
    """Record the launch: ``launched``, ``home`` (kept on the home screen) or ``off``."""
    _LAUNCH[serial] = state


def forget_run(serial: str) -> None:
    """Drop the per-start marks of *serial* before a new app stage."""
    for marks in (_SKIPPED, _TESTER, _LAUNCH):
        marks.pop(serial, None)


def reset() -> None:
    """Forget every mark (tests)."""
    for marks in (_SKIPPED, _TESTER, _LAUNCH):
        marks.clear()


async def _tester_note(serial: str, launched: bool) -> str:
    """The briefing line for an install the server did through App Tester."""
    package, fresh = _TESTER.pop(serial, ("", False))
    if not package:
        return ""
    version = await imes_upgrade.installed_version(serial, package)
    have = "`" + package + "`" + (" v" + version if version else "")
    how = "installed just now" if fresh else "already the latest build"
    return (
        "> App Tester step done by this server: "
        + have
        + " is installed ("
        + how
        + ")"
        + (" and the app was launched" if launched else "")
        + ". Do NOT repeat the App Tester or install steps of the goal by hand; "
        "continue from the app's first screen.\n\n"
    )


def _home_note(state: str) -> str:
    if state != "home":
        return ""
    return (
        "> Launch skipped: the goal starts from the home screen and no install "
        "step ran, so the app was not opened. Open it the way the goal says.\n\n"
    )


async def take_note(serial: str) -> str:
    """Markdown for the install steps done or skipped on *serial* (once), or ``""``."""
    state = _LAUNCH.pop(serial, "launched")
    tester = await _tester_note(serial, state == "launched")
    target, asked = _SKIPPED.pop(serial, ("", ""))
    if not target:
        return tester or _home_note(state)
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

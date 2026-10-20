"""Notes for the start reply about install steps the server already did or skipped."""

from __future__ import annotations

from tools.mobile import imes_upgrade
from tools.untrusted import single_line

_SKIPPED: dict[str, tuple[str, str]] = {}
_TESTER: dict[str, tuple[str, bool]] = {}
_EVIDENCE: dict[str, str] = {}
_LAUNCH: dict[str, str] = {}
_HOME: dict[str, str] = {}


def mark_home_checked(serial: str, seen: str) -> None:
    """Record the server's own home-screen check (what it saw) for the reply."""
    _HOME[serial] = seen


def home_checked(serial: str) -> bool:
    """Whether the server already checked the home screen in this start."""
    return serial in _HOME


def mark_skipped(serial: str, target: str, asked: str) -> None:
    """Record that the *asked* install of *target* was skipped on *serial*."""
    _SKIPPED[serial] = (target, asked)


def mark_tester_install(serial: str, package: str, fresh: bool) -> None:
    """Record that the App Tester stage ran for *package* (*fresh*: downloaded now)."""
    _TESTER[serial] = (package, fresh)


def mark_tester_evidence(serial: str, evidence: str) -> None:
    """Record what the server saw of the App Tester install (row, Open, version)."""
    if evidence:
        _EVIDENCE[serial] = evidence


def tester_ran(serial: str) -> bool:
    """Whether the App Tester install stage ran for *serial* in this start."""
    return serial in _TESTER


def mark_launch(serial: str, state: str) -> None:
    """Record the launch: ``launched``, ``home`` (kept on the home screen) or ``off``."""
    _LAUNCH[serial] = state


def forget_run(serial: str) -> None:
    """Drop the per-start marks of *serial* before a new app stage."""
    for marks in (_SKIPPED, _TESTER, _EVIDENCE, _LAUNCH, _HOME):
        marks.pop(serial, None)


def reset() -> None:
    """Forget every mark (tests)."""
    for marks in (_SKIPPED, _TESTER, _EVIDENCE, _LAUNCH, _HOME):
        marks.clear()


def _evidence_text(serial: str) -> str:
    """The server's own facts of the install: they answer a "found in the App
    Tester list" check, so the host does not scroll the list again."""
    seen = _EVIDENCE.pop(serial, "")
    return "; server saw: " + single_line(seen, 300) if seen else ""


async def _tester_note(serial: str, launched: bool) -> str:
    """The briefing line for an install the server did through App Tester."""
    package, fresh = _TESTER.pop(serial, ("", False))
    if not package:
        return ""
    version = await imes_upgrade.installed_version(serial, package)
    have = "`" + single_line(package) + "`"
    have += " v" + single_line(version, 40) if version else ""
    how = "installed just now" if fresh else "already the latest build"
    return (
        "> App Tester step done by this server: "
        + have
        + " is installed ("
        + how
        + ")"
        + (" and the app was launched" if launched else "")
        + _evidence_text(serial)
        + ". Do NOT repeat the App Tester or install steps of the goal by hand; "
        "continue from the app's first screen.\n\n"
    )


def _home_note(serial: str) -> str:
    """The briefing line for the home-screen check the server did first."""
    seen = _HOME.pop(serial, "")
    if not seen:
        return ""
    return (
        "> Home screen check done by this server before the app steps: "
        + single_line(seen, 200)
        + ". Cite this for the goal's home-screen check; do not press Home again.\n\n"
    )


_NOT_LAUNCHED = {
    "home": "the goal starts from the home screen and no install step ran",
    "off": "launch=false was passed",
}


def _launch_note(state: str) -> str:
    """The reply line for a start where the server did not open the app, or must
    still show the home screen the goal asked to confirm."""
    if state == "launched+check":
        return (
            "> The goal asks to confirm the home screen AFTER the app step. Send "
            '{"op": "home"} and confirm the home screen in that result; Home closes '
            'the app, so a {"op": "launch"} must follow and the reopen is expected.\n\n'
        )
    if state in _NOT_LAUNCHED:
        return (
            "> App NOT opened ("
            + _NOT_LAUNCHED[state]
            + "): the app under test is not open and the first screen is whatever "
            "the device shows. Open it the way the goal says, or re-call without "
            "launch=false to have the server open it.\n\n"
        )
    return ""


async def take_note(serial: str) -> str:
    """Markdown for the install steps done or skipped on *serial* (once), or ``""``."""
    state = _LAUNCH.pop(serial, "launched")
    home = _home_note(serial)
    tester = home + await _tester_note(serial, state.startswith("launched"))
    target, asked = _SKIPPED.pop(serial, ("", ""))
    if not target:
        return tester + _launch_note(state)
    version = await imes_upgrade.installed_version(serial, target)
    have = "already installed" + (" v" + version if version else "")
    return home + (
        "> Skipped: install of `"
        + single_line(target)
        + "` (source `"
        + single_line(asked)
        + "`), "
        + have
        + ". Ask the user whether they want a reinstall or update; "
        "do not reinstall on your own.\n\n"
    )

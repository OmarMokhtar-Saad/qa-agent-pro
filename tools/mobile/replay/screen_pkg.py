"""Replay package vocabulary: system dialogs, launchers, the platform."""

from __future__ import annotations

from tools.mobile import perception

#: Packages whose screen is an OVERLAY: the app under test is still running
#: underneath, so `back` dismisses this and the case continues. A permission
#: dialog is the one a mistaken tap produces most often -- on 2026-09-04 a tap
#: on a Voice mode control opened the microphone prompt, and the replay went on
#: tapping against it until the case ran out of escapes.
#: Re-exported from ``perception``, which is where the answer has to be taken:
#: the overlay's package is gone from the pruned elements by the time this
#: module sees a screen, because it is the same on every element of an ordinary
#: one. The set has ONE definition and this name is kept for its readers.
SYSTEM_DIALOG_PACKAGES: frozenset = perception.SYSTEM_DIALOG_PACKAGES


#: Launchers, named ONLY so the reason can say "the home screen" rather than a
#: package name a tester would have to look up. The left-the-app rule itself is
#: "a package that is not the one under test" and does not consult this set --
#: a list of launchers would be a list to keep up to date, and the rule must
#: hold for the ones nobody thought of.
LAUNCHER_PACKAGES: frozenset = frozenset(
    {
        "com.google.android.apps.nexuslauncher",
        "com.android.launcher",
        "com.android.launcher2",
        "com.android.launcher3",
        "com.sec.android.app.launcher",
    }
)


#: Ops that leave the app ON PURPOSE. Reporting one as an accident would stop a
#: script doing exactly what it asked to do.
LEAVES_ON_PURPOSE: frozenset = frozenset({"home", "open_url"})


#: The trace outcomes that mean THE DEVICE, not the app, ended the action.
#:
#: Defined here, beside the four sites that mint them, because this module invents
#: the vocabulary and ``report.error_classes`` only counts it. A set restated in the
#: report would be a second producer of the same meaning, and the drift would be
#: silent: a new outcome minted below would simply stop being counted, and the page
#: would show a smaller number with nothing to say it was smaller.
#:
#: ``tests/mobile/test_mobile_report_derivations.py`` scans THIS FILE for every
#: literal assigned to ``entry["outcome"]`` and requires the two to agree, so a name
#: added at a mint site and forgotten here fails by name on its first run.
DEVICE_ERROR_OUTCOMES: frozenset = frozenset(
    {"dump_failed", "device_error", "system_dialog", "left_app"}
)


def system_dialog_package(screen: object) -> str:
    """The system-dialog package on this screen, or ``""``.

    ANY element's, not the dominant one's. A permission prompt is a CARD over a
    full-screen app window, so it loses on area every time -- and keying the
    dialog rule on the screen's dominant package made it inert for exactly the
    packages it exists for. The dominant package answers a different question
    ("whose screen is this") and still answers it for the left-the-app rule.
    """
    if not isinstance(screen, dict):
        return ""
    named = str(screen.get("dialog_package") or "")
    if named in SYSTEM_DIALOG_PACKAGES:
        return named
    # The whole screen IS the dialog -- a full-screen permission prompt, or a
    # dump that held nothing else.
    whole = str(screen.get("package") or "")
    return whole if whole in SYSTEM_DIALOG_PACKAGES else ""


def system_dialog_detail(package: str) -> str:
    return (
        "a system dialog from "
        + str(package)[:80]
        + " is in front of the app, so nothing further was replayed against it. "
        "The app is still running underneath: answer the dialog, or send `back` "
        "to dismiss it, then continue the case."
    )


#: Windows that belong to the platform rather than to an app: an ANR ("X isn't
#: responding"), a crash dialog. They are `package="android"` and they are
#: DOMINANT, so they reach the left-app branch rather than the dialog one --
#: and `android` deliberately does not join SYSTEM_DIALOG_PACKAGES, because it
#: also appears on ordinary screens and the membership rule that keeps that set
#: safe is worth more than this one case.
#:
#: What has to change instead is the ADVICE. `launch` cannot clear an ANR.
PLATFORM_PACKAGES: frozenset = frozenset({"android"})


def _platform_detail(package: str, expected: str) -> str:
    return (
        "the screen now belongs to the Android platform ("
        + str(package)[:80]
        + ") rather than to "
        + str(expected)[:80]
        + ' -- this is usually a system dialog such as "isn\'t responding" '
        "in front of the app, so nothing further was replayed. Answer it "
        "using the buttons on the screen above, or send `back`. Do NOT send "
        "`launch`: it does not clear a dialog like this."
    )


def left_app_detail(package: str, expected: str) -> str:
    if str(package) in PLATFORM_PACKAGES:
        return _platform_detail(package, expected)
    where = (
        "the home screen (" + str(package)[:80] + ")"
        if str(package) in LAUNCHER_PACKAGES
        else "another app (" + str(package)[:80] + ")"
    )
    return (
        "the screen now belongs to "
        + where
        + " and not to "
        + str(expected)[:80]
        + ", so nothing further was replayed. If the case should continue "
        "there (an installer or launcher that opens the app under test, say), "
        "declare it with `app` or `expect_apps` in the reply and carry on. "
        "If that screen is a mistake: Send `launch` to bring the app under "
        "test back to the front."
    )

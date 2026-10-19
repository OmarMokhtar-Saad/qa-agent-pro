"""App Tester UI: the consent page, and the host's next step when the installer stops.

The installer taps only the screens it knows. Anything else (a consent the
tester must give, a page it cannot read) stops it with nothing tapped, and the
reply hands the decision to the host with the tool calls that carry it out.
"""

from __future__ import annotations

from tools.mobile.install_source import InstallOutcome

#: The one-time page App Tester shows before a tester's first build of an app.
CONSENT_RID = ":id/consentPage"

#: The "All apps" row of an app page; the consent page ignores the Back key.
BACK_TO_LIST_RID = ":id/backToAppListButton"


def consent_required(package: str) -> InstallOutcome:
    """The stop on the consent page: agreeing is the tester's choice."""
    return InstallOutcome(
        False,
        "consent_required",
        "App Tester shows its one-time consent page for "
        + repr(str(package)[:80])
        + ": the tester agrees that Firebase shares their name and device "
        'details with the app\'s developers (the "I consent" box, then START '
        "TESTING ON THIS DEVICE). This server never agrees for the tester. "
        "Nothing on that page was tapped.",
    )


def host_next(serial: str, tester: str) -> str:
    """How the host carries on itself after the installer stopped."""
    run = (
        'qa_mobile_test(device_id="' + serial + '", source="installed_package", '
        'package="' + tester + '", apply=true, goal="<what to do in App Tester>")'
    )
    return (
        "**You decide the next step; the installer stopped, the device is free.** "
        "Ask the tester when the screen needs their choice; never tick a "
        "consent box without the tester's own yes. To act in App Tester "
        "yourself, open it as a run with `"
        + run
        + "`, tap with `qa_submit_mobile_step`, end it with `qa_mobile_stop`, "
        'then call `qa_mobile_test` with `source="app_tester"` again.'
    )

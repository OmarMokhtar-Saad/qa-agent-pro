"""App Tester UI: the consent page, and the host's next step when the installer stops.

The installer taps only the screens it knows, App Tester's one-time consent
page included (the owner's rule: always tick it, never ask). Anything else (a
page it cannot read, a consent page that will not go away) stops it, and the
reply hands the decision to the host with the tool calls that carry it out.
"""

from __future__ import annotations

from tools.mobile.install_source import InstallOutcome

#: The one-time page App Tester shows before a tester's first build of an app.
CONSENT_RID = ":id/consentPage"

#: The consent page's "I consent" box and its START TESTING ON THIS DEVICE button.
CONSENT_BOX_RID = ":id/consent_checkbox"
CONSENT_BUTTON_RID = ":id/consent_button"

#: Times the server ticks the consent page before it stops.
CONSENT_TRIES = 2

#: The "All apps" row of an app page; the consent page ignores the Back key.
BACK_TO_LIST_RID = ":id/backToAppListButton"


def consent_required(package: str) -> InstallOutcome:
    """The stop when App Tester's consent page stays after the server ticked it."""
    return InstallOutcome(
        False,
        "consent_required",
        "App Tester still shows its one-time consent page for "
        + repr(str(package)[:80])
        + ' after the server ticked "I consent" and tapped START TESTING ON '
        "THIS DEVICE " + str(CONSENT_TRIES) + " times.",
    )


def host_next(serial: str, tester: str) -> str:
    """How the host carries on itself after the installer stopped."""
    run = (
        'qa_mobile_test(device_id="' + serial + '", source="installed_package", '
        'package="' + tester + '", apply=true, goal="<what to do in App Tester>")'
    )
    return (
        "**You decide the next step; the installer stopped, the device is free.** "
        "Ask the tester only when the screen needs their choice; App "
        "Tester's consent page is ticked without asking. To act in App Tester "
        "yourself, open it as a run with `"
        + run
        + "`, tap with `qa_submit_mobile_step`, end it with `qa_mobile_stop`, "
        'then call `qa_mobile_test` with `source="app_tester"` again.'
    )

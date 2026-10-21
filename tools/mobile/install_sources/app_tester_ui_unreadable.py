"""App Tester UI: the stop reply when the list could not be read at all, and the
labels one scan teaches (so the next unknown label may need no scan).

H15: a list that shows no row for ``NO_ROWS_S`` is not "the app is not listed";
the old reply told the tester no app matched, with an empty option list.
H16: a scan reads every row's name and bundle id; only the picked row's label was
remembered, so every other app in the list cost a full scan the first time.
"""

from __future__ import annotations

from tools.mobile.install_source import InstallOutcome
from tools.mobile.install_sources import app_tester_ui_memory as memory

#: Rows of one scan whose label is remembered (the store keeps 200 per section).
MAX_LEARNED_ROWS = 40


def list_unreadable() -> InstallOutcome:
    """Nothing in App Tester's list could be read before the scan's time-box."""
    return InstallOutcome(
        False,
        "list_unreadable",
        "App Tester's app list showed no readable row (the screen kept animating, "
        "was still loading, or uiautomator could not read it in time). Nothing was "
        "installed. Ask the user to open App Tester, wait until its list is drawn, "
        "then call `qa_mobile_test` again; or pass the package name so the list is "
        "not needed.",
    )


def learn_labels(serial: str, rows: list) -> None:
    """Remember label -> bundle for the rows one scan read (never raises)."""
    for row in rows[:MAX_LEARNED_ROWS]:
        memory.remember_seen_label(serial, row.name, row.bundle)

"""The keyed option menus' data and the helpers every menu shares.

Leaf of the ``render_*`` split of ``render.py``: the source tables, the count
word derived from them and the keyed-line renderer. Nothing here imports
another ``render_*`` module.
"""

from __future__ import annotations

#: The start-menu sources, in menu order: ``(key, line)``. The key is what a
#: handler branches on and what the mapping paragraph names, so the two cannot
#: drift apart.
MOBILE_SOURCES: tuple[tuple[str, str], ...] = (
    ("current_suite", "Run the test suite from THIS chat (the one just generated)"),
    ("stored_suite", "Run a suite this install already stored"),
    (
        "own_cases",
        "Run my own cases (paste a markdown table, or give a .csv/.xlsx path)",
    ),
    ("explore", "Explore freely towards a goal I describe (no test cases needed)"),
    ("rerun_failures", "Re-run only the cases that failed in the last run"),
    ("resume", "Resume a run that is already in progress"),
)

#: How the app under test gets onto the device. Every one of these needs
#: ``apply=true``. ``local_apk`` installs from here and ``app_tester`` taps App
#: Tester's own Download button and the system installer; ``download_url`` and
#: ``play_store`` hand the tester the device's own UI.
INSTALL_SOURCES: tuple[tuple[str, str], ...] = (
    ("local_apk", "Install an .apk from a path on this machine"),
    ("download_url", "Open a download link in the device's browser"),
    ("app_tester", "Install from Firebase App Tester on the device"),
    ("play_store", "Open the Play Store on the device and install from there"),
    (
        "installed_package",
        "Use an app that is already installed (give its package name)",
    ),
)

CAPTURE_SOURCES: tuple[tuple[str, str], ...] = (
    ("on", "Capture the app's HTTPS traffic for this run"),
    ("off", "Skip API capture for this run"),
)

_COUNT_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
}


def _count_word(number: int) -> str:
    return _COUNT_WORDS.get(int(number), str(int(number)))


def _keyed(options: tuple[tuple[str, str], ...]) -> str:
    """One line per option, each carrying the KEY the host must send back.

    Deliberately NOT numbered. The numbers were re-rendered by the host's own
    question UI, which relabels and reorders, and a returned position then
    selected a lane by index.
    """
    return "".join("- `" + key + "` — " + line + "\n" for key, line in options)

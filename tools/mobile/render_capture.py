"""The API-capture consent menu and the status line that names the capture tier.

Fourth layer of the ``render_*`` split of ``render.py``.

``capture_menu_markdown`` -- the API-capture consent menu, the FIRST thing a
tester sees about it (brief decisions 1 and 12). Keyed for the same reason
every other menu in this module is.
"""

from __future__ import annotations

from tools.mobile.render_menu import CAPTURE_SOURCES, _keyed

_CAPTURE_HEAD = (
    "## Capture the app's API calls for this run?\n\n"
    "qa-agents can install its OWN root certificate on this device to "
    "decrypt and record the HTTPS calls this run makes -- redacted "
    "before anything reaches disk. It only ever installs to the SYSTEM "
    "certificate store, because **the user certificate store is ignored "
    "by most apps on API 24+ and would not change what this report can "
    "show**. That needs root, which this lane's own default emulator "
    "(a Play Store image) and most real phones refuse -- when it is not "
    "available, capture continues without decryption and the report "
    "states why.\n\n"
    "The certificate is **kept installed on the device after this run** "
    "so a later run does not have to ask again. Remove it any time with "
    '`qa_setup_capture(action="remove", apply=true)`.\n\n'
)

_CAPTURE_TAIL = (
    "\nPass `capture` with one of the keys above and `capture_ack=true` "
    "to confirm, then call again.\n"
)


def capture_menu_markdown() -> str:
    """The API-capture consent menu (see module doc)."""
    return _CAPTURE_HEAD + _keyed(CAPTURE_SOURCES) + _CAPTURE_TAIL


def capture_line(resolved: object) -> str:
    """One status-block line naming the run's capture tier, or its refusal
    reason by name -- never a generic \"not available\"."""
    body = resolved if isinstance(resolved, dict) else {}
    if body.get("offer"):
        # Never asked about this device. Say so once, in a line the tester is
        # already reading, rather than halting the run with a menu it did not
        # ask for. Silence is not consent -- it is just nobody having asked.
        return (
            "- capture: not set up on this device — "
            'call again with `capture="on"` to record the app\'s API calls'
        )
    tier = str(body.get("tier") or "none")
    message = str(body.get("message") or "")
    line = "- capture: **" + tier + "**"
    if message:
        line += " (" + message + ")"
    return line

"""App Tester UI: Open after the install landed, and what the server saw."""

from __future__ import annotations

from typing import Any

from tools.mobile.adb_device_ui import find_center
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_PACKAGE,
    SleepFn,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_settle import SETTLE_POLL_S

OPEN_RID = APP_TESTER_PACKAGE + ":id/open_button"
#: Fresh looks (SETTLE_POLL_S apart) for Open to appear after the install.
OPEN_POLLS = 24


async def open_when_wanted(run: _Run, sleep: SleepFn) -> bool:
    """Tap the App Tester page's Open once a FRESH screen shows it (it appears
    only after the install finishes); True when tapped. Waits at most
    OPEN_POLLS looks; a button that never shows is not an error."""
    if not run.request.launch:
        return False
    for _ in range(OPEN_POLLS):
        shot = await run.ui.screen()
        if shot is not None and find_center(shot.xml, rid=OPEN_RID):
            return bool(await run.ui.tap_resource_id(OPEN_RID))
        await sleep(SETTLE_POLL_S)
    return False


def landed_evidence(run: _Run, after: Any, opened: bool) -> str:
    """One line of facts for the host: page, Open tapped, version installed."""
    parts = ["App Tester page for `" + str(run.request.package)[:80] + "`"]
    if run.card_tapped and not run.via_link:
        parts.append("row found in the list")
    if opened:
        parts.append("its Open button was shown and tapped")
    name = getattr(after, "version_name", "") or ""
    code = getattr(after, "version_code", None)
    version = (name + " " if name else "") + "(" + str(code) + ")"
    parts.append("version " + version + " installed")
    return "; ".join(parts)

"""App Tester UI: open an app's release page by its deep link (no scrolling).

App Tester's main activity takes ``https://appdistribution.firebase.google.com/
testerapps/<firebase-app-id>``. The link is tried first when the id is known;
any miss (no id, no link support, page never shows) returns False and the
caller falls back to the list scan.
"""

from __future__ import annotations

import time

from tools.mobile.install_sources import app_tester_ui_memory as memory
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_POLL_S,
    SleepFn,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_ready import page_ready

#: Longest wait for the release page after the link was handed over.
LINK_WAIT_S = 12.0


async def _page_shows(run: _Run, sleep: SleepFn) -> bool:
    """Poll the screen until the release page shows or LINK_WAIT_S passes."""
    deadline = time.monotonic() + LINK_WAIT_S
    while time.monotonic() < deadline:
        shot = await run.ui.screen()
        if shot is not None and page_ready(shot.xml):
            return True
        await sleep(APP_TESTER_POLL_S)
    return False


async def open_by_link(run: _Run, tester: str, sleep: SleepFn) -> bool:
    """Open the release page of ``run.request.package`` by its link; True once
    the page shows. False (nothing assumed) when the id is unknown, the device
    refuses the link or the page does not show in time."""
    app_id = memory.known_app_id(run.request.package)
    opener = getattr(run.ui, "open_link", None)
    if not app_id or opener is None:
        return False
    if not await opener(memory.release_url(app_id), tester):
        return False
    return await _page_shows(run, sleep)

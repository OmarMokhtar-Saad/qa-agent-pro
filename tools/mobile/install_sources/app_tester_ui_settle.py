"""App Tester UI: wait for the screen to change after a tap, not a fixed pause."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from tools.mobile.dialog_handlers import visible_texts

#: Gap between two looks at the screen while waiting for it to change.
SETTLE_POLL_S = 0.25
#: Looks before giving up; the wait ends at the first change, never later.
SETTLE_MAX_POLLS = 8


async def wait_screen_changed(
    ui: Any, before: list, sleep: Callable[[float], Awaitable[object]]
) -> bool:
    """True once the visible texts differ from *before*; False at the cap.

    A screen that never changes is not an error: the caller's own loop reads
    the screen next and decides, so this only ends the wait early.
    """
    for _ in range(SETTLE_MAX_POLLS):
        shot = await ui.screen()
        if shot is not None and list(visible_texts(shot.xml)) != list(before):
            return True
        await sleep(SETTLE_POLL_S)
    return False

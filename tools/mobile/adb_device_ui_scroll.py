"""The list-scroll swipes of ``AdbDeviceUi`` (moved from adb_device_ui.py)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from tools.mobile.adb_input import Swipe
from tools.mobile.tree_source import TreeResult

log = logging.getLogger("tools.mobile.adb_device_ui")

#: Where a list swipe starts and ends, as a percent of the screen height.
_SWIPE_START_PCT = 75
_SWIPE_END_PCT = 25


class _ScrollMixin:
    serial: str
    _adb: Any
    _last: Optional[TreeResult]

    async def scroll_down(self) -> None:
        """Swipe the list up (finger from 75% to 25% of the height, centred) so the
        next screen read shows what is below. Never raises; a device whose size is
        unknown is not swiped and the caller sees an unchanged screen."""
        await self._swipe(_SWIPE_START_PCT, _SWIPE_END_PCT)

    async def scroll_up(self) -> None:
        """The opposite swipe (25% to 75%): the next screen read shows what is above.
        Never raises, like scroll_down."""
        await self._swipe(_SWIPE_END_PCT, _SWIPE_START_PCT)

    async def _swipe(self, from_pct: int, to_pct: int) -> None:
        self._last = None
        got = await self._adb.display_size(self.serial)
        size = got.get("content") if isinstance(got, dict) else None
        if not size or len(size) < 2:
            log.info("adb_device_ui: no display size for %s", self.serial)
            return
        width, height = size[0], size[1]
        start = height * from_pct // 100
        end = height * to_pct // 100
        move = Swipe(width // 2, start, width // 2, end)
        done = await self._adb.swipe(self.serial, move)
        if done.get("error"):
            log.info("adb_device_ui: swipe failed for %s", self.serial)

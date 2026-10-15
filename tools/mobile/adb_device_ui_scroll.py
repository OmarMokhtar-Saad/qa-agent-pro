"""The list-scroll swipes of ``AdbDeviceUi`` (moved from adb_device_ui.py)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from tools.mobile.adb_input import Swipe
from tools.mobile.tree_source import TreeResult

log = logging.getLogger("tools.mobile.adb_device_ui")

#: Where a list swipe starts and ends, as a percent of the screen height. A 40%
#: drag leaves a page overlap, so a row is never skipped between two reads.
_SWIPE_START_PCT = 70
_SWIPE_END_PCT = 30
#: A slow drag (not a fling): the list stops where the finger does.
_SWIPE_MS = 600


class _ScrollMixin:
    serial: str
    _adb: Any
    _last: Optional[TreeResult]
    _size: Optional[tuple] = None

    async def scroll_down(self) -> None:
        """Drag the list up (finger from 70% to 30% of the height, centred, slowly)
        so the next screen read shows what is below. Never raises; a device whose
        size is unknown is not swiped and the caller sees an unchanged screen."""
        await self._swipe(_SWIPE_START_PCT, _SWIPE_END_PCT)

    async def scroll_up(self) -> None:
        """The opposite drag (30% to 70%): the next screen read shows what is above.
        Never raises, like scroll_down."""
        await self._swipe(_SWIPE_END_PCT, _SWIPE_START_PCT)

    async def _display_size(self) -> Optional[tuple]:
        """The display size, asked once per session (it does not change mid-run)."""
        if self._size is None:
            got = await self._adb.display_size(self.serial)
            size = got.get("content") if isinstance(got, dict) else None
            if size and len(size) >= 2:
                self._size = (size[0], size[1])
        return self._size

    async def _swipe(self, from_pct: int, to_pct: int) -> None:
        self._last = None
        size = await self._display_size()
        if size is None:
            log.info("adb_device_ui: no display size for %s", self.serial)
            return
        width, height = size
        x, start, end = width // 2, height * from_pct // 100, height * to_pct // 100
        done = await self._adb.swipe(self.serial, Swipe(x, start, x, end, _SWIPE_MS))
        if done.get("error"):
            log.info("adb_device_ui: swipe failed for %s", self.serial)

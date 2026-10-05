"""DeviceUi over adb: read the screen, tap a node that is on it, press back.

The port an on-device install source drives. Three rules:

* a tap targets the centre of a node that is PRESENT in the screen the caller
  just read -- no coordinates from anywhere else, so no blind taps;
* the central denylist of ``dialog_handlers`` (uninstall, delete, clear data,
  allow all the time, ...) applies here too, so a source cannot tap one even by
  mistake;
* the last screen is reused for the tap that follows it (one dump, not two on a
  slow device) and dropped after any action.

The screen comes from ``tree_source.select_tree_source`` (fallback only unless
the tester opted in); with no source registered it uses ``executor.dump_raw``,
the single-flight guard every raw dump outside the executor must take.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional
from xml.etree import ElementTree

from tools.mobile import adb, tree_source
from tools.mobile.dialog_handlers import TAP_RID, TAP_TEXT, DialogAction, is_denied
from tools.mobile.perception import parse_bounds
from tools.mobile.tree_source import TreeResult, TreeSource

log = logging.getLogger(__name__)

Selector = Callable[[str], Awaitable[Optional[TreeSource]]]

#: Where a list swipe starts and ends, as a percent of the screen height.
_SWIPE_START_PCT = 75
_SWIPE_END_PCT = 25


def _is_match(node: ElementTree.Element, text: str, rid: str) -> bool:
    if rid:
        return node.get("resource-id") == rid
    return bool(text) and text in (node.get("text"), node.get("content-desc"))


def find_center(xml: str, *, text: str = "", rid: str = "") -> Optional[tuple]:
    """Centre ``(x, y)`` of the first node whose text, content-desc (or
    resource-id) equals the argument and whose bounds have an area; else None."""
    try:
        root = ElementTree.fromstring(xml or "")
    except ElementTree.ParseError:
        return None
    for node in root.iter("node"):
        if not _is_match(node, text, rid):
            continue
        box = parse_bounds(node.get("bounds"))
        if box and box[2] > box[0] and box[3] > box[1]:
            return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    return None


RawDump = Callable[[str], Awaitable[dict]]


async def _guarded_dump(serial: str) -> dict:
    # Imported late: executor imports most of the package.
    from tools.mobile import executor  # noqa: PLC0415

    return await executor.dump_raw(serial)


class AdbDeviceUi:
    """Implements ``install_source.DeviceUi`` for one serial."""

    def __init__(
        self,
        serial: str,
        *,
        selector: Optional[Selector] = None,
        adb_api: object = None,
        raw_dump: Optional[RawDump] = None,
    ) -> None:
        self.serial = serial
        self._selector = selector or tree_source.select_tree_source
        self._adb = adb_api or adb
        self._raw_dump = raw_dump or _guarded_dump
        self._last: Optional[TreeResult] = None

    async def screen(self) -> Optional[TreeResult]:
        started = time.monotonic()
        source = await self._selector(self.serial)
        if source is not None:
            shot = await source.dump(self.serial, self._adb.DUMP_TIMEOUT_S)
        else:
            shot = await self._dump_fallback(started)
        self._last = shot
        return shot

    async def _dump_fallback(self, started: float) -> Optional[TreeResult]:
        got = await self._raw_dump(self.serial)
        xml = str(got.get("content") or "")
        if got.get("error") or not xml:
            log.info("adb_device_ui: no screen for %s", self.serial)
            return None
        elapsed = int((time.monotonic() - started) * 1000)
        return TreeResult(xml, elapsed, tree_source.FALLBACK_SOURCE)

    async def _tap_node(
        self, action: DialogAction, *, text: str = "", rid: str = ""
    ) -> bool:
        if is_denied(action):
            log.warning("adb_device_ui: refused a denylisted tap (%s)", action.kind)
            return False
        shot = self._last or await self.screen()
        center = find_center(shot.xml, text=text, rid=rid) if shot else None
        self._last = None
        if center is None:
            return False
        done = await self._adb.tap(self.serial, center[0], center[1])
        return not done.get("error")

    async def tap_text(self, text: str) -> bool:
        return await self._tap_node(DialogAction(TAP_TEXT, text), text=text)

    async def tap_resource_id(self, rid: str) -> bool:
        return await self._tap_node(DialogAction(TAP_RID, rid), rid=rid)

    async def press_back(self) -> None:
        self._last = None
        await self._adb.keyevent(self.serial, "KEYCODE_BACK")

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
        done = await self._adb.swipe(self.serial, width // 2, start, width // 2, end)
        if done.get("error"):
            log.info("adb_device_ui: swipe failed for %s", self.serial)

    async def launch(self, package: str) -> bool:
        self._last = None
        done = await self._adb.launch(self.serial, package)
        return not done.get("error")

    async def foreground(self) -> str:
        got = await self._adb.current_activity(self.serial)
        return str(got.get("content") or "").split("/", 1)[0]

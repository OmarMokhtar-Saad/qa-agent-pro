"""App Tester UI: reading the list screen for the scan (re-reads, logging)."""

from __future__ import annotations

import logging
import time

from tools.mobile import a11y_next
from tools.mobile.install_source import DeviceUi
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_PACKAGE,
    APP_TESTER_POLL_S,
    FOREGROUND_WAIT_S,
    LIST_LOAD_WAIT_S,
    MAX_EMPTY_REREADS,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_rows import is_loading, parse_rows

log = logging.getLogger(__name__)


async def read_rows(ui: DeviceUi, xml: str | None = None) -> list:
    """The rows on the screen. A screen that shows none is read again: while it
    shows the list's loading spinner, until rows appear or LIST_LOAD_WAIT_S
    passes; otherwise (a dump that failed) at most MAX_EMPTY_REREADS times.
    ``[]`` after that. It is never answered with the rows of an earlier screen."""
    if xml is None:  # no screen in hand: the first read is not a re-read
        xml = await _xml(ui)
    rows, xml = await _reread(ui, xml)
    if not rows:
        await _log_empty(ui, xml)
    return rows


async def _news(ui: DeviceUi, until: float) -> None:
    """Wait for the screen's next a11y generation (a short pause without a11y), at most to *until*."""
    await a11y_next.pause_for_news(ui.serial, until)


async def _reread(ui: DeviceUi, xml: str) -> tuple:
    """``(rows, xml)`` after the re-reads of an empty screen."""
    rows = parse_rows(xml)
    deadline = time.monotonic() + LIST_LOAD_WAIT_S
    misses = 0
    while not rows and misses < MAX_EMPTY_REREADS:
        if is_loading(xml) and time.monotonic() < deadline:
            await _news(ui, time.monotonic() + APP_TESTER_POLL_S)
        else:
            misses += 1
            await _foreground_settled(ui)
        shot = await ui.screen()
        xml = shot.xml if shot else ""
        rows = parse_rows(xml)
    return rows, xml


async def _log_empty(ui: DeviceUi, xml: str) -> None:
    """Say why a read came back empty (nothing else records it): xml size, whether
    it looks like a screen at all, the loading spinner, the foreground app."""
    fg = await ui.foreground()
    log.info(
        "app_tester empty read: xml_len=%d hierarchy=%s loading=%s foreground=%s",
        len(xml or ""),
        "<hierarchy" in (xml or ""),
        is_loading(xml or ""),
        fg,
    )


async def _foreground_settled(ui: DeviceUi) -> None:
    """Return once App Tester is the foreground app (a cheap read, no dump) or
    FOREGROUND_WAIT_S passes: the reread then sees a settled screen."""
    deadline = time.monotonic() + FOREGROUND_WAIT_S
    while await ui.foreground() != APP_TESTER_PACKAGE:
        if time.monotonic() >= deadline:
            return
        await _news(ui, min(deadline, time.monotonic() + APP_TESTER_POLL_S))


async def settled_rows(ui: DeviceUi) -> list:
    """One more read of the screen after a scroll, to prove it has stopped moving."""
    return await read_rows(ui, (await _xml(ui)))


async def _xml(ui: DeviceUi) -> str:
    shot = await ui.screen()
    return shot.xml if shot else ""


def log_scroll(run: _Run, direction: str, counts: tuple[int, int]) -> None:
    """Record one scroll (direction, rows on the screen, rows new to the scan) so
    the number of swipes of an install is auditable from its log."""
    seen, fresh = counts
    run.scrolls.append((direction, seen, fresh, run.pos))
    log.info(
        "app_tester scroll #%d %s: %d rows seen, %d new (page %d)",
        len(run.scrolls),
        direction,
        seen,
        fresh,
        run.pos,
    )

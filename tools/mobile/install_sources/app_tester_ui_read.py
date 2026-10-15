"""App Tester UI: reading the list screen for the scan (re-reads, logging)."""

from __future__ import annotations

import logging

from tools.mobile.install_source import DeviceUi
from tools.mobile.install_sources.app_tester_ui_consts import MAX_EMPTY_REREADS, _Run
from tools.mobile.install_sources.app_tester_ui_rows import parse_rows

log = logging.getLogger(__name__)


async def read_rows(ui: DeviceUi, xml: str = "") -> list:
    """The rows on the screen. A screen that shows none (a spinner, a dump that
    failed) is read again, at most MAX_EMPTY_REREADS times; ``[]`` after that.
    It is never answered with the rows of an earlier screen."""
    rows = parse_rows(xml)
    for _ in range(MAX_EMPTY_REREADS):
        if rows:
            return rows
        shot = await ui.screen()
        rows = parse_rows(shot.xml) if shot else []
    return rows


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

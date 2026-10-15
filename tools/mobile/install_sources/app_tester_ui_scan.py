"""App Tester UI: scrolling the app list to read its rows.

Offsets are scrolls from the screen the scan started on (rows below are
positive, rows above negative). The scan remembers the offset each row was
read at, so the tap can scroll back exactly that far instead of looking.

The end of the list is proven only by the same rows twice after a settle
read; a screen that shows no rows is read again, never taken as the end. The
scan is bounded by a wall-clock deadline (and a safety cap on scrolls).
"""

from __future__ import annotations

import time
from typing import Optional

from tools.mobile.install_sources import app_tester_ui_memory as memory
from tools.mobile.install_sources.app_tester_ui_consts import (
    LISTING_DEADLINE_S,
    MAX_LISTING_SCROLLS,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_read import (
    log_scroll,
    read_rows,
    settled_rows,
)
from tools.mobile.install_sources.app_tester_ui_rows import list_at_top


class _Scan:
    """Rows read so far, in list order, and where the scan is."""

    def __init__(self, run: _Run, rows: list, top: bool) -> None:
        self.run = run
        self.rows: list = []
        self.pos = 0
        self.top_pos: Optional[int] = 0 if top else None
        self.end = False
        self.cur: list = rows
        self.deadline = time.monotonic() + LISTING_DEADLINE_S
        run.offsets.clear()
        run.scrolls.clear()
        self.note(rows, 0, below=True)
        run.pos = 0

    @property
    def top(self) -> bool:
        return self.top_pos is not None

    def note(self, rows: list, pos: int, below: bool) -> int:
        """Record this screen's rows, read at offset *pos*; how many were new."""
        self.cur = rows or self.cur
        fresh = [r for r in rows if r not in self.rows]
        for row in fresh:
            self.run.offsets[row.bundle] = pos
        self.rows = self.rows + fresh if below else fresh + self.rows
        return len(fresh)

    def found(self) -> bool:
        return any(row.bundle == self.run.request.package for row in self.rows)

    def alive(self, steps: int, extra: int = 0) -> bool:
        return steps < MAX_LISTING_SCROLLS + extra and time.monotonic() < self.deadline

    async def _scroll(self, down: bool) -> list:
        """One scroll and the rows it shows ([] when none could be read)."""
        await (self.run.ui.scroll_down() if down else self.run.ui.scroll_up())
        return await read_rows(self.run.ui)

    async def down(self) -> None:
        """Scroll down until the package shows, the end is proven, a screen
        cannot be read, or the deadline (or the safety cap) passes."""
        steps = 0
        while not self.found() and self.alive(steps):
            steps += 1
            prev = self.cur
            rows = await self._scroll(True)
            fresh = self.note(rows, self.pos + 1, below=True)
            log_scroll(self.run, "down", (len(rows), fresh))
            if not rows:
                return
            if rows != prev:  # moved (also over rows already read: a rescan)
                self.pos += 1
            elif await settled_rows(self.run.ui) == rows:
                self.end = True
                return

    async def up(self) -> None:
        """Scroll up, reading every screen, until the same rows show twice (the
        top), a screen cannot be read, or the deadline."""
        prev, steps, limit = self.cur, 0, self.pos
        while self.alive(steps, limit):
            steps += 1
            rows = await self._scroll(False)
            if rows == prev and await settled_rows(self.run.ui) == rows:
                self.top_pos = self.pos
                log_scroll(self.run, "up", (len(rows), 0))
                return
            fresh = self.note(rows, self.pos - 1, below=False)
            self.pos -= 1
            log_scroll(self.run, "up", (len(rows), fresh))
            if not rows:
                return
            prev = rows

    async def jump(self, pages: int) -> None:
        """Scroll straight down *pages* times (no read between), read once."""
        for _ in range(pages):
            await self.run.ui.scroll_down()
            log_scroll(self.run, "down", (0, 0))
        rows = await read_rows(self.run.ui)
        self.note(rows, pages, below=True)
        self.pos = pages

    def remember(self) -> None:
        """Keep the page the package was found at (from the proven top)."""
        run, at = self.run, self.run.offsets.get(self.run.request.package)
        if at is not None and self.top_pos is not None and self.found():
            memory.remember_page(
                run.request.serial, run.request.package, at - self.top_pos
            )


async def _try_cached(scan: _Scan) -> None:
    """Go straight to the page the package was last found at and look once; a
    miss forgets the page and reads the list from its top instead."""
    run = scan.run
    page = memory.known_page(run.request.serial, run.request.package)
    if not page or scan.found():
        return
    await scan.jump(page)
    if not scan.found():
        memory.forget_page(run.request.serial, run.request.package)
        await scan.up()


async def scan_listing(run: _Run, xml: str) -> tuple:
    """Every row read in list order and whether the list was read whole (or
    the package's row was found). A remembered page is tried first; else the
    scan reads down from where the list is and stops at the package's row; only
    when it is not there are the rows above read too (never before: a known
    package costs no rewind). The top needs no scroll to prove when the first
    row is flush with the list."""
    rows = await read_rows(run.ui, xml)
    scan = _Scan(run, rows, list_at_top(xml))
    if scan.top:
        await _try_cached(scan)
    await scan.down()
    if not scan.found() and not scan.top:
        await scan.up()
    run.pos = scan.pos
    scan.remember()
    return scan.rows, scan.found() or (scan.end and scan.top)

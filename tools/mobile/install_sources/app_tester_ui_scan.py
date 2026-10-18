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
    CHEAP_EMPTY_READ_S,
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
        self.target = run.request.package or memory.known_bundle(
            run.request.serial, run.request.app_label
        )
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
        return any(row.bundle == self.target for row in self.rows)

    def past_read(self) -> bool:
        """True when the scan is below every offset read so far (not a rescan)."""
        return self.pos > max(self.run.offsets.values(), default=-1)

    def alive(self, steps: int, extra: int = 0) -> bool:
        return steps < MAX_LISTING_SCROLLS + extra and time.monotonic() < self.deadline

    async def _scroll(self, down: bool) -> list:
        """One scroll and the rows it shows ([] when none could be read)."""
        await (self.run.ui.scroll_down() if down else self.run.ui.scroll_up())
        started = time.monotonic()
        rows = await read_rows(self.run.ui)
        if not rows and time.monotonic() - started < CHEAP_EMPTY_READ_S:
            # an empty read is "unknown", not the end of the list: a fast one is
            # a screen caught mid-scroll, so one more read, not an aborted scan
            rows = await read_rows(self.run.ui)
        return rows

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
                if not fresh and self.past_read():  # nothing new below: the end
                    self.end = True
                    return
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


def remember_row(run: _Run, bundle: str) -> None:
    """Keep what finds *bundle*'s row again: its label, and the page it was read
    at (from the proven top, else from the screen the scan started on)."""
    serial = run.request.serial
    memory.remember_label(serial, run.request.app_label, bundle)
    at = run.offsets.get(bundle)
    if at is None:
        return
    if run.top_pos is not None:
        memory.remember_page(serial, bundle, at - run.top_pos)
    else:
        memory.remember_start_page(serial, bundle, at)


def _cached_page(scan: _Scan) -> Optional[int]:
    serial = scan.run.request.serial
    page = memory.known_page(serial, scan.target)
    if page is None and not scan.top:
        page = memory.known_start_page(serial, scan.target)
    return page


async def _try_cached(scan: _Scan) -> None:
    """Go straight to the page the package was last found at and look once; a
    miss forgets the page and reads the list from its top instead."""
    page = _cached_page(scan)
    if not page or scan.found():
        return
    await scan.jump(page)
    if not scan.found():
        memory.forget_page(scan.run.request.serial, scan.target)
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
    await _try_cached(scan)
    await scan.down()
    if not scan.found() and not scan.top:
        await scan.up()
    run.pos = scan.pos
    run.top_pos = scan.top_pos
    return scan.rows, scan.found() or (scan.end and scan.top)

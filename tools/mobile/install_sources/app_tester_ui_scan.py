"""App Tester UI: scrolling the app list to read its rows.

Offsets are scrolls from the screen the scan started on (rows below are
positive, rows above negative). The scan remembers the offset each row was
read at, so the tap can scroll back exactly that far instead of looking.
"""

from __future__ import annotations

from tools.mobile.install_sources.app_tester_ui_consts import (
    MAX_LISTING_SCROLLS,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_rows import list_at_top, parse_rows


class _Scan:
    """Rows read so far, in list order, and where the scan is."""

    def __init__(self, run: _Run, xml: str) -> None:
        self.run = run
        self.rows: list = []
        self.pos = 0
        self.top = list_at_top(xml)
        self.end = False
        self.cur: list = []
        run.offsets.clear()
        self.note(xml, 0, below=True)
        run.pos = 0

    def note(self, xml: str, pos: int, below: bool) -> int:
        """Record this screen's rows, read at offset *pos*; how many were new."""
        self.cur = parse_rows(xml) or self.cur
        fresh = [r for r in self.cur if r not in self.rows]
        for row in fresh:
            self.run.offsets[row.bundle] = pos
        self.rows = self.rows + fresh if below else fresh + self.rows
        return len(fresh)

    def found(self) -> bool:
        return any(row.bundle == self.run.request.package for row in self.rows)

    async def down(self) -> None:
        """Scroll down until the package shows, no new row appears (the end),
        a screen cannot be read or the cap is hit."""
        for _ in range(MAX_LISTING_SCROLLS):
            if self.found():
                return
            await self.run.ui.scroll_down()
            shot = await self.run.ui.screen()
            if not self.note(shot.xml if shot else "", self.pos + 1, below=True):
                self.end = True
                return
            self.pos += 1

    async def up(self) -> None:
        """Scroll up, reading every screen, until a screen equals the one before
        (the top), one cannot be read, or the cap: the walk back over rows
        already read plus MAX_LISTING_SCROLLS. A partial last scroll down is
        thus covered, not guessed."""
        prev = self.cur
        for _ in range(self.pos + MAX_LISTING_SCROLLS):
            await self.run.ui.scroll_up()
            shot = await self.run.ui.screen()
            rows = parse_rows(shot.xml) if shot else prev
            if rows == prev:
                self.top = True
                return
            self.pos -= 1
            self.note(shot.xml if shot else "", self.pos, below=False)
            prev = rows


async def scan_listing(run: _Run, xml: str) -> tuple:
    """Every row read in list order and whether the list was read whole (or
    the package's row was found). The scan reads down from where the list is
    and stops at the package's row; only when it is not there are the rows
    above read too (never before: a known package costs no rewind). The top
    needs no scroll to prove when the first row is flush with the list."""
    scan = _Scan(run, xml)
    await scan.down()
    if not scan.found() and not scan.top:
        await scan.up()
    run.pos = scan.pos
    return scan.rows, scan.found() or (scan.end and scan.top)

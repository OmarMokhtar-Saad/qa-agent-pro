"""App Tester UI: finding and tapping the app's row in the listing."""

from __future__ import annotations

from typing import Optional, Union

from tools.mobile.install_source import InstallOutcome
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_POLL_S,
    MAX_BACK_TO_LIST,
    MAX_LISTING_SCROLLS,
    SleepFn,
    _Flow,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_funcs import (
    _label_required,
    _not_on_list,
    _rows_not_found,
    _tap_failed,
    _unresolved_message,
)
from tools.mobile.install_sources.app_tester_ui_rows import (
    is_listing,
    matching_rows,
    parse_rows,
)
from tools.mobile.resolve import Candidate, PickOpts, resolve_or_ask
from tools.untrusted import wrap_untrusted

_NOT_IT = "No, another app"


class _CardMixin:
    """The listing steps of the flow."""

    async def _open_card(self: _Flow, run: _Run, xml: str) -> Optional[InstallOutcome]:
        picked = await self._pick_row(run, xml)
        if isinstance(picked, InstallOutcome):
            return picked
        return await self._tap_card(run, picked, scroll_back=True)

    async def _pick_row(self: _Flow, run: _Run, xml: str) -> Union[str, InstallOutcome]:
        """The bundle id of the app's row: the request's package when listed,
        else the one row matching the label. Several matches, or one match on
        a list not read to its end, are asked about."""
        listing = await _to_listing(run, xml, self._sleep)
        if listing is None:
            return _not_on_list()
        rows, whole = await _scan_listing(run, listing)
        if any(row.bundle == run.request.package for row in rows):
            return run.request.package
        label = run.request.app_label.strip()
        if not label:
            return _label_required()
        hits = matching_rows(label, rows)
        if not hits:
            return _rows_not_found(label, rows)
        if len(hits) == 1:
            return str(hits[0].bundle) if whole else await _confirm_row(run, hits[0])
        return await _ask_row(run, hits)

    async def _tap_card(
        self: _Flow, run: _Run, text: str, scroll_back: bool = False
    ) -> Optional[InstallOutcome]:
        """Tap the row by its bundle id. With scroll_back, a row that is not on
        the current screen is looked for by scrolling up, at most
        MAX_LISTING_SCROLLS times."""
        tapped = await run.ui.tap_text(text)
        for _ in range(MAX_LISTING_SCROLLS if scroll_back else 0):
            if tapped:
                break
            await run.ui.scroll_up()
            tapped = await run.ui.tap_text(text)
        if not tapped:
            return _tap_failed(text)
        run.card_tapped = True
        await self._sleep(APP_TESTER_POLL_S)
        return None


async def _to_listing(run: _Run, xml: str, sleep: SleepFn) -> Optional[str]:
    """App Tester reopens on the last app page viewed: press back until the
    app list shows, at most MAX_BACK_TO_LIST times. The list's XML or None."""
    for _ in range(MAX_BACK_TO_LIST):
        if is_listing(xml):
            return xml
        await run.ui.press_back()
        xml = await _read_until_listing(run, sleep)
    return xml if is_listing(xml) else None


async def _read_until_listing(run: _Run, sleep: SleepFn) -> str:
    """Re-read the screen until the list shows, at most MAX_BACK_TO_LIST reads."""
    xml = ""
    for _ in range(MAX_BACK_TO_LIST):
        shot = await run.ui.screen()
        xml = shot.xml if shot is not None else ""
        if is_listing(xml):
            break
        await sleep(APP_TESTER_POLL_S)
    return xml


async def _rewind(run: _Run, xml: str) -> str:
    """Scroll up until the list stops moving (its top), at most
    MAX_LISTING_SCROLLS times, so rows above the start are read too."""
    for _ in range(MAX_LISTING_SCROLLS):
        await run.ui.scroll_up()
        shot = await run.ui.screen()
        if shot is None or parse_rows(shot.xml) == parse_rows(xml):
            break
        xml = shot.xml
    return xml


async def _scan_listing(run: _Run, xml: str) -> tuple:
    """From the top, scroll the list until the package's row shows, a scroll
    adds no new row (end of list), a screen cannot be read, or
    MAX_LISTING_SCROLLS scrolls were made. Returns (every row read in list
    order, whether the scan stopped before the scroll cap)."""
    rows = parse_rows(xml)
    if not any(row.bundle == run.request.package for row in rows):
        rows = parse_rows(await _rewind(run, xml))
    for _ in range(MAX_LISTING_SCROLLS):
        if any(row.bundle == run.request.package for row in rows):
            return rows, True
        await run.ui.scroll_down()
        shot = await run.ui.screen()
        fresh = [r for r in parse_rows(shot.xml if shot else "") if r not in rows]
        if not fresh:
            return rows, True
        rows += fresh
    return rows, any(row.bundle == run.request.package for row in rows)


async def _confirm_row(run: _Run, row) -> Union[str, InstallOutcome]:
    """One match on a list not read to its end: the tester confirms it,
    since another match may sit below the scroll cap."""
    shown = Candidate(row.bundle, row.name).display()
    question = "Only part of the App Tester list was read. Is this the app?"
    if run.ask is not None and await run.ask(question, [shown, _NOT_IT]) == shown:
        return str(row.bundle)
    listed = wrap_untrusted("app tester options", shown)
    return InstallOutcome(
        False,
        "app_ambiguous",
        question + "\n" + listed + "\nAsk the user; do not guess.",
    )


async def _ask_row(run: _Run, hits: list) -> Union[str, InstallOutcome]:
    """Several rows match the label: the tester picks one; never guessed."""
    cands = [Candidate(row.bundle, row.name) for row in hits]
    got = await resolve_or_ask(
        "", cands, PickOpts(noun="app in App Tester", ask=run.ask)
    )
    if not got.resolved:
        return InstallOutcome(False, "app_ambiguous", _unresolved_message(got))
    return str(got.value)

"""App Tester UI: finding and tapping the app's row in the listing."""

from __future__ import annotations

from typing import Optional, Union

from tools.mobile.install_source import InstallOutcome
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_PACKAGE,
    APP_TESTER_POLL_S,
    MAX_BACK_TO_LIST,
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
from tools.mobile.install_sources.app_tester_ui_link import open_by_link
from tools.mobile.install_sources.app_tester_ui_rows import is_listing, matching_rows
from tools.mobile.install_sources.app_tester_ui_scan import remember_row, scan_listing
from tools.mobile.resolve import Candidate, PickOpts, resolve_or_ask
from tools.untrusted import wrap_untrusted

_NOT_IT = "No, another app"


class _CardMixin:
    """The listing steps of the flow."""

    async def _open_card(self: _Flow, run: _Run, xml: str) -> Optional[InstallOutcome]:
        if await open_by_link(run, APP_TESTER_PACKAGE, self._sleep):
            run.card_tapped = True
            return None
        picked = await self._pick_row(run, xml)
        if isinstance(picked, InstallOutcome):
            return picked
        return await self._tap_card(run, picked, scroll_back=True)

    async def _pick_row(self: _Flow, run: _Run, xml: str) -> Union[str, InstallOutcome]:
        """The bundle id of the app's row: the request's package (only a hint)
        when listed and no second row fits the label, else the one row matching
        the label. Several matches, or one match on
        a list not read to its end, are asked about."""
        listing = await _to_listing(run, xml, self._sleep)
        if listing is None:
            return _not_on_list()
        picked = await _choose_row(run, await scan_listing(run, listing))
        if isinstance(picked, str):
            remember_row(run, picked)
        return picked

    async def _tap_card(
        self: _Flow, run: _Run, text: str, scroll_back: bool = False
    ) -> Optional[InstallOutcome]:
        """Tap the row by its bundle id. With scroll_back, a row off the
        screen is reached by scrolling back the offsets the scan read it at;
        a miss is a failed tap (no blind scrolling)."""
        tapped = await run.ui.tap_text(text)
        if scroll_back and not tapped:
            tapped = await _scroll_to_row(run, text)
        if not tapped:
            return _tap_failed(text)
        run.card_tapped = True
        await _wait_left_listing(run, self._sleep)
        return None


async def _choose_row(run: _Run, scanned: tuple) -> Union[str, InstallOutcome]:
    rows, whole = scanned
    label = run.request.app_label.strip()
    hits = matching_rows(label, rows) if label else []
    listed = any(row.bundle == run.request.package for row in rows)
    if listed and len(hits) < 2:
        return run.request.package
    return await _pick_by_label(run, label, (hits, rows, whole))


async def _pick_by_label(
    run: _Run, label: str, found: tuple
) -> Union[str, InstallOutcome]:
    """The row the label names: one hit is taken (confirmed when the list was
    not read whole), several are asked about."""
    if not label:
        return _label_required()
    hits = found[0]
    if not hits:
        return _rows_not_found(label, found[1])
    if len(hits) == 1:
        return str(hits[0].bundle) if found[2] else await _confirm_row(run, hits[0])
    return await _ask_row(run, hits)


async def _scroll_to_row(run: _Run, text: str) -> bool:
    """Scroll back exactly to the row's remembered offset and tap it. A row with
    no remembered offset is not looked for blindly: False."""
    want, here = run.offsets.get(text), run.pos
    if want is not None:
        for _ in range(abs(here - want)):
            await (run.ui.scroll_up() if here > want else run.ui.scroll_down())
        run.pos = want
        if await run.ui.tap_text(text):
            return True
    return False


async def _wait_left_listing(run: _Run, sleep: SleepFn) -> None:
    """After the tap, poll the screen until the list is gone (the app page is
    up), at most MAX_BACK_TO_LIST reads; the pause is only between reads."""
    for _ in range(MAX_BACK_TO_LIST):
        shot = await run.ui.screen()
        if shot is not None and not is_listing(shot.xml):
            return
        await sleep(APP_TESTER_POLL_S)


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

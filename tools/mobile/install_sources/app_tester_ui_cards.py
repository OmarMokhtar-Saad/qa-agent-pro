"""App Tester UI: finding and tapping the app's card in the listing."""

from __future__ import annotations

from typing import Optional, cast

from tools.mobile.dialog_handlers import visible_texts
from tools.mobile.install_source import InstallOutcome
from tools.mobile.install_sources.app_tester_ui_consts import (
    APP_TESTER_POLL_S,
    MAX_LISTING_SCROLLS,
    _Flow,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_funcs import (
    _is_card_text,
    _label_required,
    _tap_failed,
    _unresolved_message,
)
from tools.mobile.resolve import NONE, Candidate, PickOpts, resolve_or_ask


class _CardMixin:
    """The listing steps of the flow."""

    async def _scan_listing(self: _Flow, run: _Run, texts) -> tuple:
        """Scroll the list until the package id shows, a scroll adds no new text
        (end of list), a screen cannot be read, or MAX_LISTING_SCROLLS scrolls
        were made. Returns (package shown, every text read)."""
        seen = set(texts)
        for _ in range(MAX_LISTING_SCROLLS):
            if run.request.package in seen:
                break
            await run.ui.scroll_down()
            shot = await run.ui.screen()
            fresh = visible_texts(shot.xml) if shot is not None else set()
            if fresh <= seen:
                break
            seen |= fresh
        return run.request.package in seen, seen

    async def _open_card(self: _Flow, run: _Run, texts) -> Optional[InstallOutcome]:
        found, seen = await self._scan_listing(run, texts)
        if found:
            return await self._tap_card(run, run.request.package)
        if not run.request.app_label.strip():
            return _label_required()
        cands = [Candidate(text) for text in sorted(seen) if _is_card_text(text)]
        got = await resolve_or_ask(
            run.request.app_label,
            cands,
            PickOpts(noun="app in App Tester", ask=run.ask),
        )
        if not got.resolved:
            code = "app_not_found" if got.status == NONE else "app_ambiguous"
            return InstallOutcome(False, code, _unresolved_message(got))
        return await self._tap_card(run, cast("str", got.value), scroll_back=True)

    async def _tap_card(
        self: _Flow, run: _Run, text: str, scroll_back: bool = False
    ) -> Optional[InstallOutcome]:
        """Tap the card. With scroll_back (a label resolved over every screen the
        scan read), a card that is not on the current screen is looked for by
        scrolling up, at most MAX_LISTING_SCROLLS times."""
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

"""App Tester UI: the app page (release choice and the Update tap)."""

from __future__ import annotations

from typing import Optional, cast

from tools.mobile.install_source import InstallOutcome, release_candidates
from tools.mobile.install_sources.app_tester_ui_consts import (
    ACTION_LABELS,
    APP_TESTER_POLL_S,
    _Flow,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_funcs import (
    _explicit_release,
    _no_action,
    _tap_failed,
    _unresolved_message,
    parse_releases,
)
from tools.mobile.resolve import PickOpts, decide, resolve_or_ask


class _DetailMixin:
    """The app-page steps of the flow."""

    async def _detail(self: _Flow, run: _Run, texts) -> Optional[InstallOutcome]:
        if not (run.release_done or run.tapped or run.request.latest):
            releases = parse_releases(texts)
            if len(releases) > 1:
                return await self._tap_release(run, releases)
            run.release_done = True
        if run.tapped:
            await self._sleep(APP_TESTER_POLL_S)
            return None
        label = next((name for name in ACTION_LABELS if name in texts), "")
        if not label:
            return _no_action(texts)
        if not await run.ui.tap_text(label):
            return _tap_failed(label)
        run.tapped += 1
        await self._sleep(APP_TESTER_POLL_S)
        return None

    async def _tap_release(
        self: _Flow, run: _Run, releases: list
    ) -> Optional[InstallOutcome]:
        candidates = release_candidates(releases)
        explicit = _explicit_release(run.request, releases)
        if explicit is not None:
            got = decide("", candidates, PickOpts(noun="release", explicit=explicit))
        else:
            got = await resolve_or_ask(
                "", candidates, PickOpts(noun="release", ask=run.ask)
            )
        if not got.resolved:
            return InstallOutcome(False, "release_ambiguous", _unresolved_message(got))
        if not await run.ui.tap_text(cast("str", got.value)):
            return _tap_failed(cast("str", got.value))
        run.release_done = True
        await self._sleep(APP_TESTER_POLL_S)
        return None

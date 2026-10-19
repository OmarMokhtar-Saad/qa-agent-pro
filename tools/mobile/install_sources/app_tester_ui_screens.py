"""App Tester UI: the per-screen steps (own app, foreign dialog, tester page)."""

from __future__ import annotations

from typing import Optional

from tools.mobile import dialog_handlers
from tools.mobile.dialog_handlers import (
    BACK,
    TAP_RID,
    DialogAction,
    DialogContext,
    visible_texts,
)
from tools.mobile.install_source import DeviceUi, InstallOutcome
from tools.mobile.install_sources.app_tester_ui_consts import (
    AAB_SETUP_LABEL,
    APP_TESTER_PACKAGE,
    APP_TESTER_POLL_S,
    QUIET_POLLS,
    Seen,
    SleepFn,
    _Flow,
    _Run,
)
from tools.mobile.install_sources.app_tester_ui_funcs import (
    _aab_setup,
    _foreign_stop,
    _in_progress,
)
from tools.mobile.install_sources.app_tester_ui_handoff import (
    CONSENT_BOX_RID,
    CONSENT_BUTTON_RID,
    CONSENT_RID,
    CONSENT_TRIES,
    consent_required,
)
from tools.mobile.install_sources.app_tester_ui_ready import (
    PAGE_READY_POLLS,
    page_ready,
    read_drawn,
)
from tools.mobile.install_sources.app_tester_ui_rows import (
    AAB_SETUP_RID,
    has_rid,
    is_checked,
)


async def _consent(sleep: SleepFn, run: _Run, xml: str) -> Optional[InstallOutcome]:
    """Tick "I consent", then START TESTING; stop when the page stays."""
    if run.consent_taps >= CONSENT_TRIES:
        return consent_required(run.request.package)
    run.consent_taps += 1
    if not is_checked(xml, CONSENT_BOX_RID):
        await run.ui.tap_resource_id(APP_TESTER_PACKAGE + CONSENT_BOX_RID)
    await run.ui.tap_resource_id(APP_TESTER_PACKAGE + CONSENT_BUTTON_RID)
    await sleep(APP_TESTER_POLL_S)
    return None


async def _quiet(run: _Run) -> bool:
    """True when the install runs on in the same app and no dump is due: the
    foreground is read, the screen is not."""
    if not run.tapped:
        return False
    front = await run.ui.foreground()
    mine = (APP_TESTER_PACKAGE, run.request.package)
    same = front == run.last_front and front in mine
    run.last_front = front
    run.quiet = run.quiet + 1 if same else 0
    if not same or run.quiet >= QUIET_POLLS:
        run.quiet = 0
        return False
    return True


def _page_drawing(run: _Run, xml: str) -> bool:
    """True while the app page, not tapped on yet, is still being drawn (no
    release, no button), at most PAGE_READY_POLLS times per install."""
    if run.tapped or page_ready(xml) or run.page_waits >= PAGE_READY_POLLS:
        return False
    run.page_waits += 1
    return True


class _ScreenMixin:
    """The screen-level steps of the flow."""

    async def _step(self: _Flow, run: _Run) -> Optional[InstallOutcome]:
        if await _quiet(run):
            await self._sleep(APP_TESTER_POLL_S)
            return None
        xml = await read_drawn(run, self._sleep)
        if xml is None:
            return None
        front = await run.ui.foreground()
        if front == APP_TESTER_PACKAGE:
            return await self._tester_screen(run, xml)
        if front == run.request.package:
            return await self._own_app(run)
        return await self._foreign(run, Seen(front, xml, visible_texts(xml)))

    async def _own_app(self: _Flow, run: _Run) -> Optional[InstallOutcome]:
        if not run.tapped:
            return InstallOutcome(
                False,
                "unrecognised_screen",
                "The app itself is in front, not the App Tester. Nothing was tapped.",
            )
        await self._sleep(APP_TESTER_POLL_S)
        return None

    async def _foreign(self: _Flow, run: _Run, seen: Seen) -> Optional[InstallOutcome]:
        front, xml, texts = seen
        expected = frozenset({APP_TESTER_PACKAGE, run.request.package})
        ctx = DialogContext(run.request.serial, front, expected, xml)
        action = dialog_handlers.handle(ctx)
        if action is not None:
            await self._perform(run.ui, action)
            return None
        if _in_progress(texts):
            await self._sleep(APP_TESTER_POLL_S)
            return None
        return _foreign_stop(front, texts)

    async def _perform(self: _Flow, ui: DeviceUi, action: DialogAction) -> None:
        if action.kind == BACK:
            await ui.press_back()
        elif action.kind == TAP_RID:
            await ui.tap_resource_id(action.value)
        else:
            await ui.tap_text(action.value)
        await self._sleep(APP_TESTER_POLL_S)

    async def _tester_screen(
        self: _Flow, run: _Run, xml: str
    ) -> Optional[InstallOutcome]:
        texts = visible_texts(xml)
        if _in_progress(texts):
            await self._sleep(APP_TESTER_POLL_S)
            return None
        if not run.card_tapped:
            return await self._open_card(run, xml)
        if AAB_SETUP_LABEL in texts or has_rid(xml, AAB_SETUP_RID):
            return _aab_setup()
        if has_rid(xml, CONSENT_RID):
            return await _consent(self._sleep, run, xml)
        if _page_drawing(run, xml):
            await self._sleep(APP_TESTER_POLL_S)
            return None
        return await self._detail(run, texts)

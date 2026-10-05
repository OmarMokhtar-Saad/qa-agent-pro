"""The on-device Firebase App Tester as an InstallSource.

Drives ``dev.firebase.appdistribution`` through a :class:`DeviceUi`; no Firebase
API, CLI or credential is involved (owner decision, 2026-10-05). The flow, one
screen at a time, is bounded by ``MAX_APP_TESTER_STEPS`` steps and
``APP_TESTER_FLOW_TIMEOUT_S`` seconds:

1. open App Tester and find the app card by its display label (resolve-or-ask:
   several cards for one label are asked about, never picked);
2. on the app page, choose a release when several are listed (the request's
   release or target version code decides, otherwise ask) and tap Update /
   Download / Install ONCE;
3. hand every screen of another app (package installer, permission controller)
   to ``dialog_handlers.handle``, which taps only visible controls and never a
   destructive one;
4. stop as soon as the installed versionCode reaches the target (or rises).

Never blind-taps: a screen the flow does not recognise ends it with
``unrecognised_screen`` and nothing tapped. Never uninstalls: a signing mismatch
is reported as ``needs_confirmation="uninstall"`` and the CALLER asks the
tester. The App Tester screen strings below are UNVERIFIED against a real
device; the real-device script (verify_mobile_run_fixes.py) is the check.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from tools.device_manager import valid_package_name
from tools.mobile import adb, dialog_handlers
from tools.mobile.adb_device_ui import AdbDeviceUi
from tools.mobile.app_info import AppVersion, read_version
from tools.mobile.dialog_handlers import (
    BACK,
    TAP_RID,
    DialogAction,
    DialogContext,
    visible_texts,
)
from tools.mobile.install_errors import code_from_dialog_text, explain, to_outcome
from tools.mobile.install_source import (
    DeviceUi,
    InstallOutcome,
    InstallRequest,
    Release,
    clip_releases,
    release_candidates,
)
from tools.mobile.resolve import (
    MAX_ASK_OPTIONS,
    NONE,
    AskCb,
    Candidate,
    Resolution,
    decide,
    resolve_or_ask,
)
from tools.untrusted import wrap_untrusted

APP_TESTER_PACKAGE = "dev.firebase.appdistribution"

#: Whole flow, seconds. A slow device dumps a screen in ~11 s and an install
#: downloads over the network, so this is minutes, not seconds.
APP_TESTER_FLOW_TIMEOUT_S = 240

#: Loop bound: screens examined before the flow gives up.
MAX_APP_TESTER_STEPS = 30

#: Pause between screens while something loads or installs.
APP_TESTER_POLL_S = 1.0

ACTION_LABELS = ("Update", "Download", "Install")
OPEN_LABEL = "Open"
_PROGRESS_STARTS = ("downloading", "installing", "pending", "preparing")
_VERSION_RE = re.compile(r"\b(\d+(?:\.\d+)*)\s*\((\d+)\)")

SleepFn = Callable[[float], Awaitable[None]]
VersionFn = Callable[[str, str], Awaitable[Optional[AppVersion]]]
UiFactory = Callable[[str], DeviceUi]


@dataclass
class _Run:
    """State of one install attempt."""

    ui: DeviceUi
    request: InstallRequest
    ask: Optional[AskCb]
    before: Optional[AppVersion]
    card_tapped: bool = False
    release_done: bool = False
    tapped: int = 0


def parse_releases(texts) -> list:
    """Releases named by version texts such as ``2.0 (9)``, oldest first."""
    found = []
    seen = set()
    for text in sorted(texts):
        match = _VERSION_RE.search(text)
        if match and text not in seen:
            seen.add(text)
            found.append(Release(text, "", match.group(1), int(match.group(2))))
    return sorted(found, key=lambda rel: rel.version_code or 0)


def _in_progress(texts) -> bool:
    return any(text.lower().startswith(_PROGRESS_STARTS) for text in texts)


def _improved(
    before: Optional[AppVersion], after: Optional[AppVersion], target: Optional[int]
) -> bool:
    code = after.version_code if after else None
    if code is None:
        return False
    if target is not None:
        return code >= target
    prior = before.version_code if before else None
    return prior is None or code > prior


def _already_current(
    before: Optional[AppVersion], request: InstallRequest
) -> Optional[InstallOutcome]:
    target = request.target_version_code
    code = before.version_code if before else None
    if target is None or code is None or code < target:
        return None
    message = "Already at versionCode " + str(code) + " (target " + str(target) + ")."
    return InstallOutcome(True, "", message + " Nothing to install.", code)


def _refuse_request(request: InstallRequest) -> Optional[InstallOutcome]:
    if not valid_package_name(request.package):
        return InstallOutcome(
            False,
            "bad_package",
            "Not a valid package id: "
            + repr(str(request.package)[:60])
            + ". Ask the tester for the exact package.",
        )
    if not request.app_label.strip():
        return InstallOutcome(
            False,
            "label_required",
            "App Tester lists apps by display name. Ask the tester what the app "
            "is called in App Tester (for example Acme QA).",
        )
    return None


def _quoted(texts) -> str:
    return wrap_untrusted("app tester screen", " | ".join(sorted(texts)), limit=300)


def _unresolved_message(got: Resolution) -> str:
    shown = "\n".join(cand.display() for cand in got.options[:MAX_ASK_OPTIONS])
    return (
        got.question
        + "\n"
        + wrap_untrusted("app tester options", shown)
        + "\nAsk the user which one they mean; do not guess."
    )


def _tap_failed(text: str) -> InstallOutcome:
    return InstallOutcome(
        False,
        "tap_failed",
        "Could not tap " + repr(str(text)[:80]) + " on the App Tester screen.",
    )


def _is_card_text(text: str) -> bool:
    if len(text) < 2 or text in ACTION_LABELS or text == OPEN_LABEL:
        return False
    return _VERSION_RE.search(text) is None


def _explicit_release(request: InstallRequest, releases: list) -> Optional[str]:
    if request.release is not None:
        return request.release.key
    target = request.target_version_code
    if target is None:
        return None
    hits = [rel.key for rel in releases if rel.version_code == target]
    return hits[0] if len(hits) == 1 else None


def _no_action(texts) -> InstallOutcome:
    if OPEN_LABEL in texts:
        return InstallOutcome(
            False,
            "no_update_offered",
            "App Tester shows Open and no Update or Download: the installed "
            "build may already be the latest.",
        )
    return InstallOutcome(
        False,
        "unrecognised_screen",
        "The App Tester page has no Update, Download or Install button. "
        + _quoted(texts)
        + " Nothing was tapped.",
    )


def _foreign_stop(front: str, texts) -> InstallOutcome:
    joined = " ".join(sorted(texts))
    code = code_from_dialog_text(joined)
    if code:
        note = "The installer stopped: " + _quoted(texts)
        return to_outcome(explain(code, joined), note)
    return InstallOutcome(
        False,
        "unrecognised_screen",
        "Stopped on a screen of "
        + front
        + " that no dialog handler knows. "
        + _quoted(texts)
        + " Nothing was tapped.",
    )


class AppTesterUiSource:
    """InstallSource ``app_tester``: update or install through the App Tester UI."""

    name = "app_tester"

    def __init__(
        self,
        ui_factory: Optional[UiFactory] = None,
        *,
        versions: Optional[VersionFn] = None,
        sleep: Optional[SleepFn] = None,
    ) -> None:
        self._ui_factory = ui_factory or AdbDeviceUi
        self._versions = versions or read_version
        self._sleep = sleep or asyncio.sleep

    async def available(self, serial: str) -> bool:
        listing = await adb.installed_packages(serial)
        return APP_TESTER_PACKAGE in (listing.get("content") or [])

    async def list_releases(self, request: InstallRequest) -> list:
        """Open the app page and read its releases; taps nothing but the card."""
        run = _Run(self._ui_factory(request.serial), request, None, None)
        if not await run.ui.launch(APP_TESTER_PACKAGE):
            return []
        shot = await run.ui.screen()
        if shot is None or await self._open_card(run, visible_texts(shot.xml)):
            return []
        detail = await run.ui.screen()
        if detail is None:
            return []
        return clip_releases(parse_releases(visible_texts(detail.xml)))

    async def install(
        self, request: InstallRequest, ask: Optional[AskCb] = None
    ) -> InstallOutcome:
        refusal = _refuse_request(request)
        if refusal is not None:
            return refusal
        before = await self._versions(request.serial, request.package)
        current = _already_current(before, request)
        if current is not None:
            return current
        ui = self._ui_factory(request.serial)
        if not await ui.launch(APP_TESTER_PACKAGE):
            return InstallOutcome(
                False,
                "launch_failed",
                "Could not open the Firebase App Tester. Check that it is "
                "installed and the screen is unlocked.",
            )
        return await self._drive(_Run(ui, request, ask, before))

    async def _drive(self, run: _Run) -> InstallOutcome:
        deadline = time.monotonic() + APP_TESTER_FLOW_TIMEOUT_S
        for _ in range(MAX_APP_TESTER_STEPS):
            if time.monotonic() >= deadline:
                return await self._gave_up(run, "flow_timeout")
            landed = await self._landed(run)
            if landed is not None:
                return landed
            stop = await self._step(run)
            if stop is not None:
                return stop
        return await self._gave_up(run, "step_limit")

    async def _gave_up(self, run: _Run, code: str) -> InstallOutcome:
        landed = await self._landed(run)
        if landed is not None:
            return landed
        return InstallOutcome(
            False,
            code,
            "The App Tester flow stopped (" + code + ") before the version "
            "changed. Nothing was uninstalled.",
        )

    async def _landed(self, run: _Run) -> Optional[InstallOutcome]:
        if not run.tapped:
            return None
        after = await self._versions(run.request.serial, run.request.package)
        if not _improved(run.before, after, run.request.target_version_code):
            return None
        return InstallOutcome(
            True, "", "Installed through the App Tester.", after.version_code
        )

    async def _step(self, run: _Run) -> Optional[InstallOutcome]:
        shot = await run.ui.screen()
        if shot is None:
            await self._sleep(APP_TESTER_POLL_S)
            return None
        front = await run.ui.foreground()
        texts = visible_texts(shot.xml)
        if front == APP_TESTER_PACKAGE:
            return await self._tester_screen(run, texts)
        if front == run.request.package:
            return await self._own_app(run)
        return await self._foreign(run, front, shot.xml, texts)

    async def _own_app(self, run: _Run) -> Optional[InstallOutcome]:
        if not run.tapped:
            return InstallOutcome(
                False,
                "unrecognised_screen",
                "The app itself is in front, not the App Tester. Nothing was tapped.",
            )
        await self._sleep(APP_TESTER_POLL_S)
        return None

    async def _foreign(
        self, run: _Run, front: str, xml: str, texts
    ) -> Optional[InstallOutcome]:
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

    async def _perform(self, ui: DeviceUi, action: DialogAction) -> None:
        if action.kind == BACK:
            await ui.press_back()
        elif action.kind == TAP_RID:
            await ui.tap_resource_id(action.value)
        else:
            await ui.tap_text(action.value)
        await self._sleep(APP_TESTER_POLL_S)

    async def _tester_screen(self, run: _Run, texts) -> Optional[InstallOutcome]:
        if _in_progress(texts):
            await self._sleep(APP_TESTER_POLL_S)
            return None
        if not run.card_tapped:
            return await self._open_card(run, texts)
        return await self._detail(run, texts)

    async def _open_card(self, run: _Run, texts) -> Optional[InstallOutcome]:
        cands = [Candidate(text) for text in sorted(texts) if _is_card_text(text)]
        got = await resolve_or_ask(
            run.request.app_label,
            cands,
            noun="app in App Tester",
            ask=run.ask,
        )
        if not got.resolved:
            code = "app_not_found" if got.status == NONE else "app_ambiguous"
            return InstallOutcome(False, code, _unresolved_message(got))
        if not await run.ui.tap_text(got.value):
            return _tap_failed(got.value)
        run.card_tapped = True
        await self._sleep(APP_TESTER_POLL_S)
        return None

    async def _detail(self, run: _Run, texts) -> Optional[InstallOutcome]:
        if not (run.release_done or run.tapped):
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

    async def _tap_release(self, run: _Run, releases: list) -> Optional[InstallOutcome]:
        candidates = release_candidates(releases)
        explicit = _explicit_release(run.request, releases)
        if explicit is not None:
            got = decide("", candidates, noun="release", explicit=explicit)
        else:
            got = await resolve_or_ask("", candidates, noun="release", ask=run.ask)
        if not got.resolved:
            return InstallOutcome(False, "release_ambiguous", _unresolved_message(got))
        if not await run.ui.tap_text(got.value):
            return _tap_failed(got.value)
        run.release_done = True
        await self._sleep(APP_TESTER_POLL_S)
        return None

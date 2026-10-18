"""App Tester UI: constants, the per-install state and the mixin contract."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, NamedTuple, Optional, Protocol

from tools.mobile.app_info import AppVersion
from tools.mobile.install_source import DeviceUi, InstallOutcome, InstallRequest
from tools.mobile.resolve import AskCb

APP_TESTER_PACKAGE = "dev.firebase.appdistribution"

#: Safety cap on the scrolls of one pass over the App Tester list. The real
#: bound is LISTING_DEADLINE_S; the cap only stops a list that never ends.
MAX_LISTING_SCROLLS = 40

#: Wall-clock budget of one list scan (every scroll is a swipe plus a dump).
LISTING_DEADLINE_S = 90.0

#: Re-reads of a screen that parsed to no rows (not loading) before giving up.
#: One: each is a ~11 s dump; the reread first waits (cheap foreground poll)
#: for App Tester to be in front, so it reads a settled screen.
MAX_EMPTY_REREADS = 1
# an empty scroll read faster than this was a screen caught moving: read once more
CHEAP_EMPTY_READ_S = 3.0

#: Longest wait for App Tester to be the foreground app before that reread.
FOREGROUND_WAIT_S = 3.0

#: Longest wait for a list that shows its loading spinner to show rows.
LIST_LOAD_WAIT_S = 20.0

#: Back presses (and screen reads after each) made to leave the app page App
#: Tester reopens on and reach its app list.
MAX_BACK_TO_LIST = 3

#: Pause between screens while something loads or installs.
APP_TESTER_POLL_S = 1.0

#: While an install runs, the screen is dumped (a dump waits for the animating
#: progress bar to idle: seconds) only when the foreground package changes or
#: after this many cheap foreground polls; the version is checked every poll.
QUIET_POLLS = 4

ACTION_LABELS = ("Update", "Download", "Install")
OPEN_LABEL = "Open"
_PROGRESS_STARTS = ("downloading", "installing", "pending", "preparing")
_VERSION_RE = re.compile(r"\b(\d+(?:\.\d+)*)\s*\((\d+)\)")
#: The App Tester text of its one-time setup for AAB releases (a tester step).
AAB_SETUP_LABEL = "Get started"

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
    #: bundle id -> scroll offset from the scan's start the row was read at.
    offsets: dict = field(default_factory=dict)
    #: current offset (scrolls) from the screen the scan started on.
    pos: int = 0
    #: scroll offset of the proven list top (None when not proven).
    top_pos: Optional[int] = None
    #: install watch: the foreground package last seen, cheap polls since a dump.
    last_front: str = ""
    quiet: int = 0
    #: one (direction, rows seen, new rows, offset) entry per scroll made.
    scrolls: list = field(default_factory=list)


class Seen(NamedTuple):
    """A screen the flow could not place: the front package, its XML and texts."""

    front: str
    xml: str
    texts: Any


# What the mixins (screens, cards, detail) call on each other and on the class.
# Mirrors the methods defined in app_tester_ui_screens, _cards and _detail; the
# mypy-strict gate catches drift.
class _Flow(Protocol):
    _sleep: SleepFn
    _ui_factory: UiFactory
    _staged: Any
    _versions: VersionFn

    async def _scan_and_stage(self, run: _Run, xml: str, firebase_id: str) -> Any: ...

    async def _own_app(self, run: _Run) -> Optional[InstallOutcome]: ...

    async def _foreign(self, run: _Run, seen: Seen) -> Optional[InstallOutcome]: ...

    async def _perform(self, ui: DeviceUi, action: Any) -> None: ...

    async def _tester_screen(self, run: _Run, xml: str) -> Optional[InstallOutcome]: ...

    async def _open_card(self, run: _Run, xml: str) -> Optional[InstallOutcome]: ...

    async def _pick_row(self, run: _Run, xml: str) -> Any: ...

    async def _tap_card(
        self, run: _Run, text: str, scroll_back: bool = False
    ) -> Optional[InstallOutcome]: ...

    async def _detail(self, run: _Run, texts: Any) -> Optional[InstallOutcome]: ...

    async def _tap_release(
        self, run: _Run, releases: list
    ) -> Optional[InstallOutcome]: ...

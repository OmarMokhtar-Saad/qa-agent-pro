"""Update an installed app: its request, ports, report and summary text."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, NamedTuple, Optional

from tools.device_manager import valid_package_name
from tools.mobile.app_info import AppVersion
from tools.mobile.app_pick import PickRequest
from tools.mobile.install_source import InstallSource
from tools.mobile.resolve import AskCb, Resolution
from tools.mobile.run_verdict import OUTCOME_PASS, Verdict

#: Characters of one report message. The host model re-reads it every turn; it
#: is a verdict plus two version lines, or one question.
MAX_UPDATE_MESSAGE_CHARS = 800

STATUS_OK = "ok"
STATUS_ASK = "ask"
STATUS_CONFIRM = "confirm"
STATUS_FAILED = "failed"
STATUS_UNVERIFIED = "unverified"

SOURCE_DEFAULT = "app_tester"
CONFIRM_UNINSTALL = "uninstall"

#: App Tester outcome codes that are a question for the tester, not a failure.
ASK_CODES = frozenset({"app_ambiguous", "app_not_found", "release_ambiguous"})

#: What a port may raise. Narrow on purpose: a bug (TypeError, AttributeError)
#: must surface, and CancelledError is not an Exception so it always propagates.
PORT_FAILURES = (
    OSError,
    RuntimeError,
    TimeoutError,
    asyncio.TimeoutError,
    ValueError,
    LookupError,
)

Clock = Callable[[], float]


@dataclass(frozen=True)
class UpdateRequest:
    serial: str = ""
    app_text: str = ""
    package: str = ""
    source: str = SOURCE_DEFAULT
    app_label: str = ""
    target_version_code: Optional[int] = None


def _install_label(request: UpdateRequest) -> str:
    """The display name an install source lists the app by: the explicit label,
    else the tester's own words for the app unless they are a package id."""
    label = str(request.app_label or "").strip()
    if label:
        return label
    text = str(request.app_text or "").strip()
    return "" if valid_package_name(text) else text


@dataclass(frozen=True)
class UpdatePorts:
    """Everything update_app touches, injected so tests need no device."""

    pick_device: Callable[[str], Awaitable[Resolution]]
    pick_package: Callable[[PickRequest], Awaitable[Resolution]]
    read_version: Callable[[str, str], Awaitable[Optional[AppVersion]]]
    get_source: Callable[[str], Optional[InstallSource]]
    launch: Callable[[str, str], Awaitable[bool]]
    foreground: Callable[[str], Awaitable[str]]
    ask: Optional[AskCb] = None
    clock: Clock = time.monotonic


@dataclass(frozen=True)
class UpdateReport:
    status: str
    message: str
    serial: str = ""
    package: str = ""
    before: Optional[AppVersion] = None
    after: Optional[AppVersion] = None
    verdict: Optional[Verdict] = None
    confirm_action: str = ""
    options: tuple = ()
    timings_ms: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "serial": self.serial,
            "package": self.package,
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
            "verdict": self.verdict.line() if self.verdict else "",
            "confirmAction": self.confirm_action,
            "options": list(self.options),
            "timingsMs": dict(self.timings_ms),
        }


def _clip(text: object) -> str:
    return str(text or "").strip()[:MAX_UPDATE_MESSAGE_CHARS]


def _status(verdict: Optional[Verdict], launched: bool) -> str:
    if not launched:
        return STATUS_FAILED
    if verdict is None:
        return STATUS_UNVERIFIED
    return STATUS_OK if verdict.outcome == OUTCOME_PASS else STATUS_FAILED


def _version_line(label: str, version: Optional[AppVersion]) -> str:
    return label + ": " + (version.line() if version else "not readable") + "."


class Landing(NamedTuple):
    """What the launch step found: the package, whether it started, the versions."""

    package: str
    launched: bool
    versions: tuple


def _summary(landing: Landing, verdict: Optional[Verdict]) -> str:
    parts = []
    if not landing.launched:
        parts.append("Installed, but launching " + landing.package + " failed.")
    if verdict is None:
        parts.append(
            "Could not verify the update: the installed version was unreadable, "
            "or there was no earlier version or target to compare with."
        )
    else:
        parts.append(verdict.line())
    parts.append(_version_line("Before", landing.versions[0]))
    parts.append(_version_line("After", landing.versions[1]))
    return " ".join(parts)

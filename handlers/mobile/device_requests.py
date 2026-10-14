"""Request objects for the mobile device handlers: one value per call.

Each handler takes ONE of these instead of a long argument list, so a tool
wrapper builds it from the MCP tool's own parameters. Every field has a
default, so an empty request is the no-argument call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from handlers import _common


@dataclass(frozen=True)
class AppArgs:
    """``qa_app_info``: which app on which device (empty = ask)."""

    app: str = ""
    package: str = ""
    serial: str = ""


@dataclass(frozen=True)
class UpdateArgs:
    """``qa_update_app``: app, package, device and install source."""

    app: str = ""
    package: str = ""
    serial: str = ""
    source: str = ""


@dataclass(frozen=True)
class CapArgs:
    """``qa_setup_capture``: the device, the action and its two consents."""

    serial: str = ""
    action: str = "prepare"
    apply: bool = False
    capture_ack: bool = False


@dataclass(frozen=True)
class StatusArgs:
    """``qa_mobile_status``: which run to read and whether to render a report."""

    run_id: str = ""
    session_token: str = ""
    report_now: bool = False
    progress: _common.ProgressCb = None


@dataclass(frozen=True)
class WatchArgs:
    """``qa_network_watch``: the watch action and its device target."""

    action: str = "status"
    serial: str = ""
    package: str = ""
    apply: bool = False


class StatusView(NamedTuple):
    """What the status reply is composed from: the run, its cases, its report."""

    run_id: str
    body: dict
    recorded: list
    report: tuple[str, str]

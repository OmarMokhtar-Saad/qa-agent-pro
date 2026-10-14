"""Feature Analysis value types: the reply, the call and the stage results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple

from handlers import _common


class FeatureAnalysisReply(str):
    """The Feature Analysis reply text, plus the screens to attach as MCP
    image content.

    A ``str`` SUBCLASS on purpose. ``handle_feature_analysis`` has 17 return
    statements and 16 of them are plain text (errors, menus, cancellations);
    every caller and every existing test treats the reply as a string. This
    lets the ONE success path carry attachments without rewriting the other
    sixteen or breaking `x in reply`. ``mcp_server.qa_feature_analysis`` reads
    ``.images`` via getattr, so a plain str from any other path still works.
    """

    images: list

    def __new__(cls, text: str, images=()):
        obj = super().__new__(cls, text)
        obj.images = list(images or [])
        return obj


@dataclass(frozen=True)
class FeatureAnalysisCall:
    """The arguments of one ``handle_feature_analysis`` call."""

    feature_or_url: str = ""
    mode: str = ""
    device_id: str = ""
    choose: _common.ChooseCb = None
    progress: _common.ProgressCb = None
    jira_content_json: str = ""


class _FaTicket(NamedTuple):
    """What the ticket step read: its text and whether a URL was fetched."""

    text: str = ""
    used_url: bool = False


class _FaCapture(NamedTuple):
    """What the capture step produced: the screen count and the raw screens."""

    screens_captured: int = 0
    forwarded: tuple = ()


class _FaClosed(NamedTuple):
    """A closed submit task: its id, recorded meta, report and usability."""

    task_id: str
    meta: dict
    report: Any
    usable: bool

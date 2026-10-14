"""Parameter objects of the wizard flows: the generate request and the wizard IO."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from handlers import _common, _elicit


@dataclass(frozen=True)
class GenerateRequest:
    """The optional arguments of ``handle_generate_test_cases``."""

    attached_images: list | None = None
    proceed_anyway: bool = False
    source_plan: str = ""
    attached_image_count: int = 0
    capture_ids: list | None = None
    image_gate_ack: bool = False
    image_carry_ack: bool = False
    choose: _common.ChooseCb = None
    ask_text: _elicit.AskCb = None
    progress: _common.ProgressCb = None
    jira_content_json: str = ""
    stage_token: str = ""


class WizardIO(NamedTuple):
    """The dialog callbacks plus the generate handler the wizard flows call."""

    choose: _common.ChooseCb
    ask_text: _elicit.AskCb
    progress: _common.ProgressCb
    generate: Callable[..., Awaitable[str]]


def _prepare_kwargs(req: GenerateRequest) -> dict[str, Any]:
    """The keyword arguments ``handle_prepare_test_cases`` takes from a request."""
    return {
        "attached_images": req.attached_images,
        "proceed_anyway": req.proceed_anyway,
        "choose": req.choose,
        "ask_text": req.ask_text,
        "progress": req.progress,
        "jira_content_json": req.jira_content_json,
        "stage_token": req.stage_token,
        "source_plan": req.source_plan,
        "attached_image_count": req.attached_image_count,
        "capture_ids": list(req.capture_ids or []),
        "image_gate_ack": req.image_gate_ack,
        "image_carry_ack": req.image_carry_ack,
    }

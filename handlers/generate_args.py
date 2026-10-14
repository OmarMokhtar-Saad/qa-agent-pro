"""Parameter objects of the host-mode prepare handler (PLR0913)."""

from __future__ import annotations

from typing import NamedTuple

from agents.host_gen_types import _GateCtx, _Grounding
from handlers import _common, _elicit


class GroundArgs(NamedTuple):
    """Inputs of ``ground_and_gate`` / ``_grounding_fetch`` / ``_fetch_page_content``."""

    text: str
    attached_images: list | None = None
    proceed_anyway: bool = False
    choose: _common.ChooseCb = None
    ask_text: _elicit.AskCb = None
    progress: _common.ProgressCb = None
    audit_source: str = "prepare"
    jira_content_json: str = ""


class PreflightArgs(NamedTuple):
    """Inputs of ``_prepare_preflight``."""

    text: str
    capture_ids: list | None
    attached_images: list | None
    attached_image_count: int
    image_carry_ack: bool


class Beat1Args(NamedTuple):
    """Inputs of ``image_gate_beat1``."""

    ctx: _GateCtx
    plan: str
    source_plan: str
    choose: _common.ChooseCb
    ask_text: _elicit.AskCb


class Beat2Args(NamedTuple):
    """Inputs of ``_image_gate_beat2``."""

    grounded: _Grounding
    ctx: _GateCtx
    plan: str
    image_gate_ack: bool
    choose: _common.ChooseCb


class PlanNudgeArgs(NamedTuple):
    """Inputs of ``image_plan_nudge``."""

    plan: str
    ctx: _GateCtx
    cap_images: list
    cap_missing: list

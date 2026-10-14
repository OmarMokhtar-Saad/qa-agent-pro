"""The argument values one PREPARE call carries between its stages."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from handlers import _common, _elicit, generate

logger = logging.getLogger("tools.mcp_handlers")


@dataclass
class _PrepArgs:
    """The arguments of one handle_prepare_test_cases call, as one value.

    ``capture_ids`` and ``jira_content_json`` are replaced as the front half
    resolves them (pre-flight carry, staged payload)."""

    text: str
    attached_images: list | None
    proceed_anyway: bool
    source_plan: str
    attached_image_count: int
    capture_ids: list | None
    image_gate_ack: bool
    image_carry_ack: bool
    choose: _common.ChooseCb
    ask_text: _elicit.AskCb
    progress: _common.ProgressCb
    jira_content_json: str


@dataclass
class _PrepFront:
    """What the refusals and image gate beats resolved, for the generation half."""

    args: _PrepArgs
    carry: Any
    intake: Any
    gctx: generate._GateCtx
    plan: Any
    cap_images: Any
    cap_labels: Any
    cap_missing: Any
    missing_rendered: str = ""


@dataclass(frozen=True)
class _PrepParts:
    """The locals of ``_prepare_generate`` that ``_build_prep_run`` reads."""

    grounded: Any
    prepared: Any
    prospective: Any
    skip: Any
    notes: Any
    img: Any
    host_mode_on: bool
    checklist_job: Any

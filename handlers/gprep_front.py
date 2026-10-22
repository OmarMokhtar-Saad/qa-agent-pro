"""The front half of PREPARE: refusals, capture intake and both image gates."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from handlers import generate, image_gate
from handlers.generate_args import Beat1Args, PlanNudgeArgs
from handlers.gprep_args import _PrepArgs, _PrepFront
from handlers.gprep_early import _prepare_early_refusal
from handlers.gprep_pre import (
    _beat1_clarify,
    _peek_captured,
    _plan_nudge_if_due,
    _revive_shelved,
)

logger = logging.getLogger("tools.mcp_handlers")


class _Intaken(NamedTuple):
    """What the capture intake resolved, ahead of the image gate beats."""

    plan: Any
    peek: tuple
    intake: Any
    gctx: generate._GateCtx


def _intake_gctx(a: _PrepArgs, cap_images: list) -> tuple[Any, generate._GateCtx]:
    """The image intake and the gate context built over it."""
    intake = generate._image_intake(
        a.attached_images, cap_images, a.attached_image_count
    )
    a.attached_images = intake.attached_images
    gctx = generate._GateCtx(
        a.text, a.jira_content_json, a.attached_images, intake.attested, intake.captured
    )
    return intake, gctx


def _front_intake(a: _PrepArgs) -> _Intaken:
    """Revive the shelf, normalise the plan, peek the tray and take the intake."""
    _revive_shelved(a.capture_ids)
    plan = image_gate._normalize_source_plan(a.source_plan)
    peek = _peek_captured(a.capture_ids)
    intake, gctx = _intake_gctx(a, peek[0])
    return _Intaken(plan, peek, intake, gctx)


async def _gate_beats(
    a: _PrepArgs, i: _Intaken, staged: bool
) -> tuple[generate.PreparePayloadResult | None, Any]:
    """Image gate beat 1 and the plan nudge: (early reply or None, beat 1)."""
    b1 = await generate.image_gate_beat1(
        Beat1Args(i.gctx, i.plan, a.source_plan, a.choose, a.ask_text, staged)
    )
    if b1.menu is not None:
        menu = _beat1_clarify(b1.menu, i.peek[3])
        return generate.PreparePayloadResult(clarify=menu), b1
    nudge = await _plan_nudge_if_due(
        PlanNudgeArgs(b1.plan, i.gctx, i.peek[0], i.peek[2]), a.image_gate_ack
    )
    if nudge is not None:
        return generate.PreparePayloadResult(clarify=nudge), b1
    return None, b1


async def _prepare_front(
    a: _PrepArgs, stage_token: str
) -> generate.PreparePayloadResult | _PrepFront:
    """Everything before grounding: refusals, capture intake and both pre-gates."""
    refusal, carry = await _prepare_early_refusal(a, stage_token)
    if refusal is not None:
        return refusal
    i = _front_intake(a)
    staged = bool(stage_token.strip() and a.jira_content_json.strip())
    early, b1 = await _gate_beats(a, i, staged)
    if early is not None:
        return early
    return _PrepFront(a, carry, i.intake, i.gctx, b1.plan, *i.peek)

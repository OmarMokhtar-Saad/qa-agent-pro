"""The input, pre-flight, open-prep and page-shape refusals of PREPARE."""

from __future__ import annotations

import logging
from typing import Any

from agents.host_gen_args import HintArgs
from handlers import generate
from handlers.gprep_args import _PrepArgs

logger = logging.getLogger("tools.mcp_handlers")


def _input_refusal(
    a: _PrepArgs, stage_token: str
) -> generate.PreparePayloadResult | None:
    """The input refusal; resolves ``a.jira_content_json`` in place."""
    refusal, a.jira_content_json = generate._prepare_input_refusal(
        a.text, a.jira_content_json, stage_token
    )
    if refusal:
        return generate.PreparePayloadResult(clarify=refusal)
    return None


async def _preflight_refusal(
    a: _PrepArgs,
) -> tuple[generate.PreparePayloadResult | None, Any, Any]:
    """The pre-flight refusal, the pre-flight carry and the open prep."""
    refusal, carry, prep = await generate._prepare_preflight(
        generate.PreflightArgs(
            a.text,
            a.capture_ids,
            a.attached_images,
            a.attached_image_count,
            a.image_carry_ack,
        )
    )
    if refusal:
        return generate.PreparePayloadResult(clarify=refusal), carry, prep
    return None, carry, prep


def _open_prep_clarify(a: _PrepArgs, prep: Any) -> generate.PreparePayloadResult | None:
    """The open-prep clarify, which `proceed_anyway` dismisses."""
    if prep and not a.proceed_anyway:
        hint = generate._pending_image_gate_hint(
            HintArgs(
                a.text,
                a.source_plan,
                a.jira_content_json,
                a.attached_images,
                a.attached_image_count,
                a.capture_ids,
            )
        )
        return generate.PreparePayloadResult(
            clarify=generate._open_prep_refusal(prep) + hint
        )
    return None


def _page_shape_refusal(a: _PrepArgs) -> generate.PreparePayloadResult | None:
    """The refusal for a Jira page that names no issue."""
    refusal = generate._jira_page_without_issue_note(a.text)
    if refusal:
        return generate.PreparePayloadResult(clarify=refusal)
    return None


async def _prepare_early_refusal(
    a: _PrepArgs, stage_token: str
) -> tuple[generate.PreparePayloadResult | None, Any]:
    """The input, pre-flight, open-prep and page-shape refusals, in order.

    Returns (refusal or None, the pre-flight carry). Resolves
    ``a.jira_content_json`` and ``a.capture_ids`` in place."""
    early = _input_refusal(a, stage_token)
    if early is not None:
        return early, None
    early, carry, prep = await _preflight_refusal(a)
    if early is not None:
        return early, carry
    a.capture_ids = carry.capture_ids
    early = _open_prep_clarify(a, prep)
    if early is None:
        early = _page_shape_refusal(a)
    return early, carry

"""Host mode: image intake, ticket recency notes and generation run (C3c).

Moved from handlers/generate.py. Every call that reaches a handler module, or a
name tests patch on ``handlers.generate``, goes through the hook groups.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.host_gen_args import GenRequest
from agents.host_gen_hooks import GenerateHooks
from agents.host_gen_types import (
    PreparePayloadResult,
    ProgressCb,
    _Grounding,
    _ImageIntake,
    _TicketNotes,
)
from agents.scenario_args import GenerationRequest
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


# Drop empty attachments on EVERY call (a host that sends [{}] supplied no
# screen), then join captured device screens. `or None` keeps the None-vs-[]
# distinction for every downstream reader.
def _merge_attachments(attached_images: list | None, cap_images: list) -> list | None:
    with never_raise(logger, "merging captured screens failed", exc_info=True) as g:
        attached_images = [i for i in (attached_images or []) if i] or None
        if cap_images:
            attached_images = list(attached_images or [])
            attached_images = attached_images + cap_images
    if g.failed:
        attached_images = cap_images or None
    return attached_images


# The chat-ATTESTED count; the host's number is never verifiable here.
def _attested_count(attached_image_count: int) -> int:
    try:
        return max(0, int(attached_image_count or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


# Device-captured screens are the SECOND intake channel: screens HANDED to the
# chat client on this payload (a later byte budget may still drop some).
def _captured_count(cap_images: list) -> int:
    # pragma: no cover - a count never breaks a prepare
    with never_raise(logger, "counting captured screens failed", exc_info=True):
        return len([i for i in (cap_images or []) if i])
    return 0


def image_intake(
    attached_images: list | None, cap_images: list, attached_image_count: int
) -> _ImageIntake:
    """Drop empty attachments, merge captured screens, count both channels."""
    merged = _merge_attachments(attached_images, cap_images)
    attested = _attested_count(attached_image_count)
    return _ImageIntake(merged, attested, _captured_count(cap_images))


# Both values are computed BEFORE the envelope is written, so the stamp and the
# disclosure can never disagree. Stale-snapshot asks "is this the CURRENT
# revision"; the condensed note asks "is this the WHOLE of it"; the recent-suite
# warning only warns, it never blocks generation.
async def _stale_and_recent(text: str, updated: str, hooks: GenerateHooks) -> tuple:
    stale = await hooks.run.stale_snapshot_note(text, updated)
    return stale, ""  # the existing-suite notice at the top of the reply replaces it


async def ticket_recency_notes(
    text: str, grounded: _Grounding, hooks: GenerateHooks
) -> _TicketNotes:
    """The ticket's stale-snapshot, recent-suite and condensed-payload notes."""
    notes = hooks.notes
    updated = notes.safe_snapshot_stamp((grounded.url_content or {}).get("updated"))
    stale, recent = await _stale_and_recent(text, updated, hooks)
    chars = notes.jira_content_chars(grounded.url_content)
    condensed = await notes.condensed_payload_note(text, updated, chars)
    return _TicketNotes(updated, stale, recent, chars, condensed)


# An early return from the prepare generation (unreadable source / no real
# feature text) carries the tester-facing message, which already NAMES what was
# missing; it is passed through unchanged. The refusal leaves an audit row
# (capped, single line, machine-safe) so "it asked me again" is traceable.
async def _rejected(
    req: GenRequest, prepared: tuple, hooks: GenerateHooks
) -> PreparePayloadResult:
    _reject_msg = str(prepared[0] or "")
    await hooks.common.audit(
        "mcp_prepare_rejected",
        detail={
            "reason": " ".join(_reject_msg.split())[:120],
            "source_kind": "url" if hooks.common.is_url(req.text) else "text",
        },
    )
    return PreparePayloadResult(
        clarify=_reject_msg + hooks.capture.retry_hint(req.capture_ids)
    )


def _generation_request(req: GenRequest, on_status: Any) -> GenerationRequest:
    g = req.grounded
    return GenerationRequest(
        req.text,
        g.url_content,
        g.ui_content,
        attached_images=req.attached_images,
        openapi_text=g.openapi_text,
        on_status=on_status,
    )


async def run_prepare_generation(
    req: GenRequest, progress: ProgressCb, hooks: GenerateHooks
) -> PreparePayloadResult | Any:
    """Run the prepare generation; an early-return tuple becomes an audited clarify."""

    async def _on_status(msg: str) -> None:
        await hooks.common.emit(progress, msg)

    prepared = await hooks.run.prepare_generation(_generation_request(req, _on_status))
    if isinstance(prepared, tuple):
        return await _rejected(req, prepared, hooks)
    return prepared
